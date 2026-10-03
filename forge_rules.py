# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_rules.py - answers to the simulator's rules questions, read from Forge card scripts.

game_actions.py asks things like "how does this land enter?", "what can this permanent tap for?",
"is this a fetchland, and what does it fetch?". This module answers them from the parsed Forge
script (see forge_scripts.py) instead of pattern-matching Oracle text.

Every function either answers or raises ForgeUnsupported / returns None when a script uses
something the simulator does not model. game_actions then falls back to the older Scryfall-text
parsing for that card, so a strange script never breaks the game.

Forge (https://github.com/Card-Forge/forge) is GPL-3.0. The card scripts are only read from the
user's own cache folder; see the note in forge_scripts.py before ever redistributing them.
"""
import re
from dataclasses import dataclass, replace

from forge_scripts import BASIC_LAND_MANA, ForgeUnsupported, compare, parse_cost
from mana_system import ALL_COLORS, ManaOption

ALL_MANA = ALL_COLORS + ("C",)


def face_of(store, name, face=0):
    """The ForgeFace for a card, or None when the store has no script for it (yet)."""
    peek = getattr(store, "peek_forge", None)
    if peek is None:
        return None
    card = peek(name)
    return card.face(face) if card else None


# ---------------------------------------------------------------------------
# opening-hand effects (Gemstone Caverns, Leylines)
# ---------------------------------------------------------------------------
def opening_hand_effects(ff):
    """Effects a card lets you use from your opening hand, as a list of dicts:
    {"name", "not_first", "counter": (TYPE, n) or None, "exile": n}.

    Forge writes them as K:MayEffectFromOpeningHand:<SVar>[:!PlayFirst]; the SVar chain is
    ChangeZone(self, hand -> battlefield) then optionally PutCounter(self) and ChangeZone(hand -> exile).
    Anything else (Chancellors reveal their card, for instance) raises ForgeUnsupported.
    """
    out = []
    for kw in ff.keywords:
        parts = kw.split(":")
        if parts[0] != "MayEffectFromOpeningHand":
            continue
        if len(parts) < 2:
            raise ForgeUnsupported(f"odd opening-hand keyword {kw!r}")
        step = ff.svar_params(parts[1])
        if not step or step.get("DB") != "ChangeZone" or step.get("Defined") != "Self" \
                or step.get("Origin") != "Hand" or step.get("Destination") != "Battlefield":
            raise ForgeUnsupported(f"opening-hand effect {parts[1]} is not 'put onto the battlefield'")
        effect = {"name": parts[1], "not_first": len(parts) > 2 and parts[2].lower() == "!playfirst",
                  "counter": None, "exile": 0}
        seen = set()
        nxt = step.get("SubAbility")
        while nxt:
            if nxt in seen:
                raise ForgeUnsupported("looping SubAbility")
            seen.add(nxt)
            sub = ff.svar_params(nxt)
            if not sub:
                raise ForgeUnsupported(f"missing SVar {nxt}")
            api = sub.get("DB")
            if api == "PutCounter" and sub.get("Defined") == "Self":
                effect["counter"] = (sub.get("CounterType", "").upper(), int(sub.get("CounterNum", "1")))
            elif api == "ChangeZone" and sub.get("Origin") == "Hand" and sub.get("Destination") == "Exile" \
                    and sub.get("ChangeType") == "Card":
                effect["exile"] = int(sub.get("ChangeNum", "1"))
            else:
                raise ForgeUnsupported(f"opening-hand sub-effect {api}")
            nxt = sub.get("SubAbility")
        out.append(effect)
    return out


# ---------------------------------------------------------------------------
# entering the battlefield
# ---------------------------------------------------------------------------
@dataclass
class EntryContext:
    """What entering-the-battlefield conditions need to know about the game."""
    count: object = None          # callable(valid_expr) -> how many of your permanents match it
    opponents: int = 1


def _eval_count(ff, svar_name, ctx):
    raw = ff.svars.get(svar_name, "")
    if raw == "PlayerCountOpponents$Amount":
        return ctx.opponents
    raise ForgeUnsupported(f"count '{raw or svar_name}'")


def entry_policy(ff, ctx):
    """How does this permanent enter? Returns (tapped, life_to_pay, note).

    Reads the 'Event$ Moved ... Destination$ Battlefield' replacement effects: plain "enters
    tapped", "unless you control N or fewer other lands", "unless you have two or more
    opponents" and shock lands ("you may pay N life; if you don't, it enters tapped": the
    default here is to pay).
    """
    for r in ff.replacements:
        if r.get("Event") != "Moved" or r.get("Destination") != "Battlefield" or r.get("ValidCard") != "Card.Self":
            continue
        sv = ff.svar_params(r.get("ReplaceWith", ""))
        if sv is None or sv.get("DB") != "Tap":
            continue                      # some other replacement (handled by entry_prompt, or not at all)
        if "UnlessCost" in sv:
            cost = parse_cost(sv["UnlessCost"])
            if sv.get("UnlessPayer", "You") == "You" and cost.life and not (cost.mana or cost.unsupported):
                return False, cost.life, ""
            raise ForgeUnsupported(f"UnlessCost {sv['UnlessCost']}")
        if "ConditionPresent" in sv:
            n = ctx.count(sv["ConditionPresent"])
            return compare(n, sv.get("ConditionCompare", "GE1")), 0, ""
        if "ConditionCheckSVar" in sv:
            n = _eval_count(ff, sv["ConditionCheckSVar"], ctx)
            return compare(n, sv.get("ConditionSVarCompare", "GE1")), 0, ""
        if any(k.startswith("Condition") for k in sv):
            raise ForgeUnsupported("enters-tapped condition")
        return True, 0, ""
    return False, 0, ""


def doesnt_untap(ff):
    """'This artifact doesn't untap during your untap step' (Mana Vault, Basalt Monolith)."""
    return any(r.get("Event") == "Untap" and r.get("ValidCard") == "Card.Self" and r.get("Layer") == "CantHappen"
               for r in ff.replacements)


def has_haste(ff):
    return any(k.split(":")[0] == "Haste" for k in ff.keywords)


def entry_prompt(ff):
    """A choice the player must make when this permanent enters, or None.

    {"kind": "imprint", "valid": expr}         Chrome Mox: you may exile a matching card from hand
    {"kind": "discard_or_bin", "valid": expr}  Mox Diamond: discard a matching card, or it goes to the graveyard
    """
    for t in ff.triggers:
        if (t.get("Mode") == "ChangesZone" and t.get("ValidCard") == "Card.Self"
                and t.get("Destination") == "Battlefield" and "Execute" in t):
            sv = ff.svar_params(t["Execute"])
            if (sv and sv.get("DB") == "ChangeZone" and sv.get("Imprint") == "True"
                    and sv.get("Origin") == "Hand" and sv.get("Destination") == "Exile"
                    and sv.get("ChangeNum", "1") == "1" and "ChangeType" in sv):
                return {"kind": "imprint", "valid": sv["ChangeType"]}
    for r in ff.replacements:
        if r.get("Event") == "Moved" and r.get("Destination") == "Battlefield" and r.get("ValidCard") == "Card.Self":
            sv = ff.svar_params(r.get("ReplaceWith", ""))
            if sv and sv.get("DB") == "Discard" and sv.get("Optional") == "True" and "DiscardValid" in sv:
                return {"kind": "discard_or_bin", "valid": sv["DiscardValid"]}
    return None


# ---------------------------------------------------------------------------
# fetchlands
# ---------------------------------------------------------------------------
def describe_valid(expr, desc=""):
    """'Mountain,Forest' -> 'Mountain or Forest'; 'Land.Basic' -> 'basic land'."""
    if desc:
        return desc
    if expr == "Land.Basic":
        return "basic land"
    parts = [p.split(".")[0] if p.split(".")[0] not in ("Card", "Permanent") else p for p in expr.split(",")]
    return " or ".join(parts)


def fetch_spec(ff):
    """{'life', 'types', 'expr', 'what', 'tapped'} for a fetchland face, else None."""
    if not ff.is_land:
        return None
    for ab in ff.abilities:
        if ab.get("AB") != "ChangeZone" or ab.get("Origin") != "Library" or ab.get("Destination") != "Battlefield":
            continue
        cost = parse_cost(ab.get("Cost", ""))
        if not (cost.tap and cost.sac_self) or cost.mana or cost.unsupported or cost.sac_other or cost.tap_other:
            continue
        expr = ab.get("ChangeType")
        if not expr:
            continue
        return {"life": cost.life, "types": expr.split(","), "expr": expr,
                "what": describe_valid(expr, ab.get("ChangeTypeDesc", "")),
                "tapped": ab.get("Tapped") == "True"}
    return None


# ---------------------------------------------------------------------------
# mana abilities
# ---------------------------------------------------------------------------
@dataclass
class ManaContext:
    """What mana abilities need to know about the game."""
    identity: tuple = ()          # your commander's colour identity
    artifacts: int = 0            # artifacts you control (metalcraft)
    counters: dict = None         # counters on this permanent, e.g. {"LUCK": 1}
    reflect_colors: object = None  # callable(valid_expr, property) -> set of colours (raises ForgeUnsupported)


@dataclass
class ManaAbilities:
    options: list
    manual: list          # abilities the simulator can't automate
    hand: list            # options usable from the hand (Elvish Spirit Guide)
    inactive: list        # abilities that produce nothing at the moment (Chrome Mox with no imprint)


_MANA_KEYS = {"AB", "Cost", "Produced", "Amount", "SpellDescription", "SubAbility", "Activation", "PrecostDesc",
              "ActivationZone", "RestrictValid", "AddsNoCounter", "ConditionCheckSVar", "ConditionSVarCompare",
              "AILogic", "AIManaPref", "Valid", "ColorOrType", "ReflectProperty", "Defined", "Secondary"}
_SUB_KEYS = {"DB", "Produced", "Amount", "ConditionCheckSVar", "ConditionSVarCompare", "NumDmg", "Defined",
             "SubAbility", "SpellDescription", "Valid", "ColorOrType", "ReflectProperty", "AILogic"}
_SPEND_ONLY = re.compile(r"Spend this mana only[^.]*\.")


def _svar_value(ff, name, ctx):
    raw = ff.svars.get(name, "")
    m = re.fullmatch(r"Count\$CardCounters\.(\w+)", raw)
    if m:
        return (ctx.counters or {}).get(m.group(1).upper(), 0)
    raise ForgeUnsupported(f"count '{raw or name}'")


def _condition_holds(ff, step, ctx):
    for key in step:
        if key.startswith("Condition") and key not in ("ConditionCheckSVar", "ConditionSVarCompare"):
            raise ForgeUnsupported(key)
    if "ConditionCheckSVar" not in step:
        return True
    return compare(_svar_value(ff, step["ConditionCheckSVar"], ctx), step.get("ConditionSVarCompare", "GE1"))


def _productions(step, ctx):
    """The alternatives ({colour: count}) one Mana / ManaReflected step can add."""
    kind = step.get("AB") or step.get("DB")
    if kind == "ManaReflected":
        valid, prop = step.get("Valid"), step.get("ReflectProperty")
        if not valid or prop not in ("Is", "Produce") or step.get("ColorOrType", "Color") != "Color":
            raise ForgeUnsupported("reflected mana")
        if ctx.reflect_colors is None:
            raise ForgeUnsupported("reflected mana needs the game")
        colours = ctx.reflect_colors(valid, prop)
        return [{c: 1} for c in ALL_COLORS if c in colours]
    produced = step.get("Produced")
    amount = step.get("Amount", "1")
    if not produced or not amount.isdigit():
        raise ForgeUnsupported("Produced/Amount")
    amount = int(amount)
    tokens = produced.split()
    if tokens[0] == "Combo" or tokens == ["Any"]:
        if amount != 1:
            raise ForgeUnsupported("Combo with Amount")
        colours = []
        for t in (tokens[1:] if tokens[0] == "Combo" else ["Any"]):
            if t == "Any":
                colours += list(ALL_COLORS)
            elif t == "ColorIdentity":
                colours += list(ctx.identity)
            elif t in ALL_MANA:
                colours.append(t)
            else:
                raise ForgeUnsupported(f"Produced {produced}")
        return [{c: 1} for c in dict.fromkeys(colours)]
    prod = {}
    for t in tokens:
        if t not in ALL_MANA:
            raise ForgeUnsupported(f"Produced {produced}")
        prod[t] = prod.get(t, 0) + amount
    return [prod]


def _combine(a, b):
    """Two mana-adding steps that both happen (one of them has no choice)."""
    if len(a) != 1 and len(b) != 1:
        raise ForgeUnsupported("two mana choices in one ability")
    out = []
    for x in a:
        for y in b:
            merged = dict(x)
            for c, n in y.items():
                merged[c] = merged.get(c, 0) + n
            out.append(merged)
    return out


def _tap_damage(ff):
    """Damage to you whenever this permanent becomes tapped (City of Brass)."""
    total = 0
    for t in ff.triggers:
        if t.get("Mode") == "Taps" and t.get("ValidCard") == "Card.Self" and "Execute" in t:
            sv = ff.svar_params(t["Execute"])
            if sv and sv.get("DB") == "DealDamage" and sv.get("Defined") == "You" and sv.get("NumDmg", "").isdigit():
                total += int(sv["NumDmg"])
            else:
                raise ForgeUnsupported("tap trigger")
    return total


def _one_ability(ff, ab, ctx, tap_damage):
    """Turn one 'AB$ Mana' / 'AB$ ManaReflected' line into (options, zone)."""
    if set(ab) - _MANA_KEYS:
        raise ForgeUnsupported(f"parameters {sorted(set(ab) - _MANA_KEYS)}")
    cost = parse_cost(ab.get("Cost", ""))
    if cost.unsupported or cost.mana or cost.sac_other or cost.discard_self:
        raise ForgeUnsupported("cost")
    if cost.tap_other and cost.tap_other[0] != 1:
        raise ForgeUnsupported("tap more than one other permanent")
    zone = ab.get("ActivationZone", "Battlefield")
    if zone == "Hand":
        if not cost.exile_self_from_hand or cost.tap or cost.life or cost.sac_self or cost.tap_other:
            raise ForgeUnsupported("hand cost")
        where = "hand"
    elif zone == "Battlefield":
        if not cost.tap or cost.exile_self_from_hand:
            raise ForgeUnsupported("mana ability without {T}")
        where = "battlefield"
    else:
        raise ForgeUnsupported(f"ActivationZone {zone}")

    condition = ""
    if ab.get("Activation"):
        if ab["Activation"] != "Metalcraft":
            raise ForgeUnsupported(f"Activation {ab['Activation']}")
        condition = "metalcraft"

    chain, node = [ab], ab
    while node.get("SubAbility"):
        sub = ff.svar_params(node["SubAbility"])
        if sub is None or set(sub) - _SUB_KEYS or len(chain) > 4:
            raise ForgeUnsupported("sub-ability")
        chain.append(sub)
        node = sub

    productions, damage = None, tap_damage
    for step in chain:
        if not _condition_holds(ff, step, ctx):
            continue
        kind = step.get("AB") or step.get("DB")
        if kind in ("Mana", "ManaReflected"):
            prods = _productions(step, ctx)
            productions = prods if productions is None else _combine(productions, prods)
        elif kind == "DealDamage":
            if step.get("Defined") != "You" or not step.get("NumDmg", "").isdigit():
                raise ForgeUnsupported("damage")
            damage += int(step["NumDmg"])
        else:
            raise ForgeUnsupported(f"sub-ability {kind}")
    if not productions:
        return [], where

    restrict, note = "", ""
    m = _SPEND_ONLY.search(ab.get("SpellDescription", ""))
    if ab.get("RestrictValid") or m:
        restrict = ab.get("RestrictValid") or "restricted"
        note = m.group(0) if m else "Spend this mana only on certain spells."
    tap_other = (cost.tap_other[0], cost.tap_other[1]) if cost.tap_other else ()
    return [ManaOption(prod, life_cost=cost.life, damage=damage, sacrifice=cost.sac_self,
                       condition=condition, note=note, restrict=restrict, tap_other=tap_other,
                       exile_self=(where == "hand"))
            for prod in productions], where


def mana_abilities(ff, ctx):
    """Everything this card face can do for mana, read from its Forge script."""
    result = ManaAbilities([], [], [], [])
    try:
        tap_damage = _tap_damage(ff)
    except ForgeUnsupported:
        result.manual.append("(a tap trigger this simulator doesn't model)")
        tap_damage = 0
    for ab in ff.abilities:
        if ab.get("AB") not in ("Mana", "ManaReflected"):
            continue
        try:
            opts, where = _one_ability(ff, ab, ctx, tap_damage)
        except ForgeUnsupported:
            result.manual.append(ab.get("SpellDescription") or ab.get("Cost", "mana ability"))
            continue
        if not opts:
            result.inactive.append(ab.get("SpellDescription") or "mana ability")
        (result.hand if where == "hand" else result.options).extend(opts)
    # Basic land types carry an intrinsic "{T}: Add ..." that Forge scripts leave out.
    for t in ff.types:
        colour = BASIC_LAND_MANA.get(t)
        if colour and not any(o.produces == {colour: 1} and not o.life_cost and not o.tap_other for o in result.options):
            result.options.append(ManaOption({colour: 1}, damage=0))
    return result


# ---------------------------------------------------------------------------
# "whenever you tap a permanent for mana, add one more" (Kinnan)
# ---------------------------------------------------------------------------
def mana_bonus_triggers(ff):
    """[{'valid': 'Permanent.nonLand', 'colour_only': False}] for each Kinnan-style trigger on the card."""
    found = []
    for t in ff.triggers:
        if t.get("Mode") != "TapsForMana" or t.get("Activator", "You") != "You":
            continue
        sv = ff.svar_params(t.get("Execute", ""))
        if (sv and sv.get("DB") == "ManaReflected" and sv.get("ReflectProperty") == "Produced"
                and sv.get("ColorOrType") in ("Type", "Color")):
            found.append({"valid": t.get("ValidCard", "Card"), "colour_only": sv["ColorOrType"] == "Color"})
    return found


def apply_bonus(option, n=1, colour_only=False):
    """Add n extra mana of a type the option produces (the first, in W U B R G C order)."""
    order = ALL_MANA if not colour_only else ALL_COLORS
    first = next((c for c in order if c in option.produces), None)
    if first is None:
        return option
    produces = dict(option.produces)
    produces[first] += n
    return replace(option, produces=produces, bonus=first)
