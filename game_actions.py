# SPDX-License-Identifier: GPL-3.0-or-later
"""
game_actions.py - the rules-side actions the table GUI calls (no pygame in here).

Every action takes the game state and a Player, changes them, writes a line to
the game log, and returns a result the GUI can show. Card data comes from a
CardDataStore's *cache* (store.peek_card), never the network, so these are
safe to call from the GUI thread.

What is simulated: turns (untap, draw), land drops, casting with automatic mana
payment, mana abilities, commander tax, Kinnan-style "add one more mana" triggers,
enters-tapped lands, fetchlands, imprint / discard-on-entry choices, mulligans and
moving cards between zones. What is NOT: the stack, priority, targeting,
triggered/activated abilities other than mana abilities. Spell effects are
resolved by hand (draw, tutor, etc. with the zone menus).

Where the rules come from: if the store has the card's Forge script
(store.peek_forge, see forge_scripts.py / forge_rules.py) the script decides. Cards
without a script, or with a script that uses something unmodelled, fall back to
parsing the Scryfall Oracle text.
"""
import random
import re
from dataclasses import replace

import forge_rules as fr
from forge_scripts import ForgeUnsupported, matches_valid, type_words
from mana_system import (
    ALL_COLORS, COLOR_ORDER, card_face, mana_options, parse_mana_cost, plan_payment,
)

# Anything a Forge script can throw at us that means "can't interpret it": use the fallback instead.
_FORGE_ERRORS = (ForgeUnsupported, KeyError, ValueError, TypeError, IndexError, AttributeError)

HAND_SIZE = 7
# CR 103.5e (multiplayer games): the first mulligan does not count toward the
# number of cards put on the bottom. Set to 0 for a strict two-player game.
FREE_MULLIGANS = 1

ZONE_ATTR = {
    "hand": "hand", "library": "library", "graveyard": "graveyard",
    "exile": "exile", "command": "command_zone",
}
ZONE_LABEL = {
    "hand": "hand", "library": "library", "graveyard": "graveyard", "exile": "exile",
    "command": "command zone", "battlefield": "battlefield",
}
PERMANENT_TYPES = ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle", "Land")
NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9}
KINNAN_TRIGGER = "Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def info_of(store, name):
    return store.peek_card(name) if store else None


def _face_text(info, face=0, key="oracle_text"):
    return card_face(info, face).get(key) or ""


def type_line_of(store, name, face=0):
    return _face_text(info_of(store, name), face, "type_line")


def is_land_name(store, name, face=0):
    return "Land" in type_line_of(store, name, face)


def cost_text(cost):
    """{'generic': 2, 'U': 1} -> '{2}{U}'."""
    out = ""
    if cost.get("generic"):
        out += f"{{{cost['generic']}}}"
    for c in COLOR_ORDER:
        out += f"{{{c}}}" * cost.get(c, 0)
    return out or "{0}"


def commander_identity(player, store):
    colors = set()
    for name in player.commander_names:
        info = info_of(store, name)
        if info:
            colors.update(info.get("color_identity", []))
    return tuple(c for c in ALL_COLORS if c in colors)


def kinnan_bonus_active(player, store):
    for e in player.battlefield:
        info = info_of(store, e["name"])
        if info and KINNAN_TRIGGER in _face_text(info, e.get("face", 0)):
            return True
    return False


def count_artifacts(player, store):
    return sum("Artifact" in type_line_of(store, e["name"], e.get("face", 0)) for e in player.battlefield)


def _has_haste(text):
    return bool(re.search(r"(?m)(^|, )Haste(,|$)", text))


def _forge(store, name, face=0):
    """The Forge script face for a card, or None if the store has none (yet)."""
    return fr.face_of(store, name, face)


def card_colors(store, name, face=0):
    info = info_of(store, name)
    if not info:
        return set()
    fd = card_face(info, face)
    colours = fd.get("colors") or info.get("colors")
    if not colours:                       # Scryfall always sends "colors"; saved test data may not: use the cost
        colours = re.findall(r"[WUBRG]", fd.get("mana_cost") or "")
    return set(colours)


def entry_matches(store, entry, expr, is_self=False, controller="you"):
    """Does a permanent match a Forge 'valid' expression like 'Creature.Legendary+YouCtrl'?"""
    info = info_of(store, entry["name"])
    if not info:
        return False
    face = entry.get("face", 0)
    fd = card_face(info, face)
    return matches_valid(expr, type_words(fd.get("type_line")), card_colors(store, entry["name"], face),
                         controller=controller, is_self=is_self, tapped=bool(entry.get("tapped")))


def count_matching(player, store, expr):
    """How many permanents this player controls match a Forge valid expression."""
    return sum(1 for e in player.battlefield if entry_matches(store, e, expr))


def mana_bonuses(player, store, entry):
    """One flag per 'whenever you tap X for mana, add one more' trigger that applies to `entry`.

    Each flag says whether the extra mana must be a colour (True) or may be any type (False).
    Kinnan, Bonder Prodigy is the example: nonland permanents give one extra mana.
    """
    found = []
    for e in player.battlefield:
        ff = _forge(store, e["name"], e.get("face", 0))
        if ff is not None:
            try:
                for trig in fr.mana_bonus_triggers(ff):
                    if entry_matches(store, entry, trig["valid"], is_self=(e is entry)):
                        found.append(trig["colour_only"])
            except _FORGE_ERRORS:
                pass
        else:
            info = info_of(store, e["name"])
            if info and KINNAN_TRIGGER in _face_text(info, e.get("face", 0)) and not entry.get("is_land"):
                found.append(False)
    return found


# ---------------------------------------------------------------------------
# entering the battlefield
# ---------------------------------------------------------------------------
def land_entry_policy(info, face, player, gs):
    """How does this land enter? Returns (tapped, life_to_pay, note).

    Understands: plain 'enters tapped', 'unless you control N or fewer/more other
    lands', 'unless you have two or more opponents', and shock lands ('you may pay
    N life. If you don't, it enters tapped'; the default is to pay the life).
    """
    text = _face_text(info, face)
    other_lands = sum(1 for e in player.battlefield if e.get("is_land"))
    opponents = max(0, len(gs.players) - 1)

    m = re.search(r"you may pay (\d+) life\. If you don't, it enters tapped", text)
    if m:
        return False, int(m.group(1)), ""
    m = re.search(r"enters tapped unless you control (\w+) or (fewer|more) other lands", text)
    if m and m.group(1) in NUMBER_WORDS:
        n = NUMBER_WORDS[m.group(1)]
        untapped = other_lands <= n if m.group(2) == "fewer" else other_lands >= n
        return (not untapped), 0, ""
    m = re.search(r"enters tapped unless you have (\w+) or more opponents", text)
    if m and m.group(1) in NUMBER_WORDS:
        return opponents < NUMBER_WORDS[m.group(1)], 0, ""
    for line in text.split("\n"):
        if re.search(r"enters tapped\.", line) and "unless" not in line:
            return True, 0, ""
    if "enters tapped unless" in text:
        return False, 0, "Check the card text: it may enter tapped."
    return False, 0, ""


def entry_policy(info, name, face, player, gs, store):
    """How does this permanent enter? Returns (tapped, life_to_pay, note). Forge script first."""
    ff = _forge(store, name, face)
    if ff is not None:
        try:
            return fr.entry_policy(ff, fr.EntryContext(
                count=lambda expr: count_matching(player, store, expr),
                opponents=max(0, len(gs.players) - 1)))
        except _FORGE_ERRORS:
            pass                                  # unmodelled script: fall back to the card text
    if "Land" in _face_text(info, face, "type_line"):
        return land_entry_policy(info, face, player, gs)
    return False, 0, ""


def prompt_choices(player, prompt, store):
    """Hand cards that can be chosen for an entry prompt: [(hand_index, name), ...]."""
    out = []
    for i, name in enumerate(player.hand):
        info = info_of(store, name)
        if not info:
            continue
        try:
            fd = card_face(info, 0)
            if matches_valid(prompt["valid"], type_words(fd.get("type_line")), card_colors(store, name)):
                out.append((i, name))
        except ForgeUnsupported:
            continue
    return out


def enter_battlefield(gs, player, name, store, tapped=None, face=0):
    """Put a card onto the battlefield and return (entry, note).

    tapped=None applies the card's own enters-tapped rules; True/False forces it.
    Cards that ask for a choice when they enter get entry["awaiting"] set (Chrome Mox: imprint,
    Mox Diamond: discard a land, shock lands: pay life or enter tapped); the GUI asks and calls
    resolve_prompt. Until then the permanent can't be tapped for mana.
    """
    info = info_of(store, name)
    land = bool(info) and "Land" in _face_text(info, face, "type_line")
    entry = {"name": name, "face": face, "tapped": False, "counters": {},
             "summoning_sick": True, "is_land": land}
    note = ""
    if info and tapped is None:
        auto_tapped, life, note = entry_policy(info, name, face, player, gs, store)
        entry["tapped"] = auto_tapped
        if life:                      # "you may pay N life; if you don't, it enters tapped": the player decides
            entry["tapped"] = True
            entry["awaiting"] = {"kind": "pay_or_tap", "life": life}
            note = f"pay {life} life to keep it untapped" + (f"; {note}" if note else "")
    elif tapped:
        entry["tapped"] = True
    player.battlefield.append(entry)

    ff = _forge(store, name, face)
    prompt = fr.entry_prompt(ff) if ff is not None else None
    if prompt and "awaiting" not in entry:
        if prompt_choices(player, prompt, store):
            entry["awaiting"] = prompt
        elif prompt["kind"] == "discard_or_bin":
            player.battlefield.pop()
            player.graveyard.append(name)
            note = (note + "; " if note else "") + "no card to discard, so it goes to the graveyard"
        else:
            note = (note + "; " if note else "") + "nothing to imprint"
    return entry, note


def resolve_prompt(gs, player, entry, hand_index, store):
    """Answer an entry prompt. Returns (ok, message).

    hand_index is the chosen hand card (imprint, discard) or None to decline. For a shock land
    ("pay_or_tap") pass True to pay the life and keep it untapped, anything else to leave it tapped.
    """
    prompt = entry.get("awaiting")
    if not prompt:
        return False, f"{entry['name']} isn't waiting for a choice."
    if not any(e is entry for e in player.battlefield):
        return False, f"{entry['name']} is no longer on the battlefield."
    if prompt["kind"] == "pay_or_tap":
        entry.pop("awaiting")
        if hand_index is True:
            player.life -= prompt["life"]
            entry["tapped"] = False
            msg = f"{entry['name']}: paid {prompt['life']} life, it enters untapped (life {player.life})."
        else:
            msg = f"{entry['name']}: not paid, it enters tapped."
        gs._log(f"{player.name}: {msg}")
        return True, msg
    if hand_index is not None:
        if hand_index not in [i for i, _ in prompt_choices(player, prompt, store)]:
            return False, "That card can't be chosen (or is no longer in your hand)."
    entry.pop("awaiting")
    name = entry["name"]
    if prompt["kind"] == "imprint":
        if hand_index is None:
            msg = f"{name}: nothing imprinted."
        else:
            card = player.hand.pop(hand_index)
            player.exile.append(card)
            entry["imprinted"] = [card]
            msg = f"{name}: exiled {card} from hand (imprint)."
    else:                                         # discard_or_bin
        if hand_index is None:
            for i, e in enumerate(player.battlefield):
                if e is entry:
                    player.battlefield.pop(i)
                    break
            player.graveyard.append(name)
            msg = f"{name}: no land discarded, so it goes to the graveyard."
        else:
            card = player.hand.pop(hand_index)
            player.graveyard.append(card)
            msg = f"{name}: discarded {card}."
    gs._log(f"{player.name}: {msg}")
    return True, msg


def awaiting_entry(player):
    """The first permanent that is waiting for a choice, or None."""
    return next((e for e in player.battlefield if e.get("awaiting")), None)


# ---------------------------------------------------------------------------
# moving cards between zones
# ---------------------------------------------------------------------------
def zone_list(player, zone):
    return getattr(player, ZONE_ATTR[zone])


def move_card(gs, player, src, index, dst, store=None, tapped=None, to_top=True, face=0):
    """Move one card between zones. `index` is its position in the source zone.

    A commander sent to the graveyard or exile returns to the command zone.
    Returns (ok, message).
    """
    if src == "battlefield":
        if not 0 <= index < len(player.battlefield):
            return False, "That permanent is no longer there."
        name = player.battlefield.pop(index)["name"]
    else:
        cards = zone_list(player, src)
        if not 0 <= index < len(cards):
            return False, "That card is no longer there."
        name = cards.pop(index)

    suffix = ""
    if name in player.commander_names and dst in ("graveyard", "exile") and src != "command":
        dst, suffix = "command", " (commander returns to the command zone)"

    if dst == "battlefield":
        _, note = enter_battlefield(gs, player, name, store, tapped=tapped, face=face)
        suffix += f" ({note})" if note else ""
    elif dst == "library":
        player.library.insert(0, name) if to_top else player.library.append(name)
        suffix += " (top)" if to_top else " (bottom)"
    else:
        zone_list(player, dst).append(name)

    msg = f"{player.name}: {name} {ZONE_LABEL[src]} -> {ZONE_LABEL[dst]}{suffix}"
    gs._log(msg)
    return True, msg


def shuffle_library(gs, player):
    random.shuffle(player.library)
    gs._log(f"{player.name} shuffles their library.")


def adjust_life(gs, player, delta):
    player.life += delta
    gs._log(f"{player.name}'s life {'+' if delta > 0 else ''}{delta} = {player.life}")


# ---------------------------------------------------------------------------
# mana
# ---------------------------------------------------------------------------
def _opponent_land_colours(gs, player, store):
    """Colours that lands an opponent controls could produce (Fellwar Stone)."""
    colours = set()
    for opp in gs.players:
        if opp is player:
            continue
        for e in opp.battlefield:
            if e.get("is_land"):
                opts = _mana_abilities(gs, opp, e, store, allow_reflect=False)[0]
                for o in opts:
                    colours |= {c for c in o.produces if c in ALL_COLORS}
    return colours


def _mana_abilities(gs, player, entry, store, allow_reflect=True):
    """(options, manual_lines, inactive_lines, hand_options) for one permanent (or a card in hand).

    Read from the Forge script when the store has one, otherwise from the Scryfall text.
    """
    name, face = entry["name"], entry.get("face", 0)
    identity = commander_identity(player, store)
    ff = _forge(store, name, face)
    if ff is not None:
        def reflect(valid, prop):
            if valid.startswith("Defined.Imprinted"):
                out = set()
                for n in entry.get("imprinted", []):
                    out |= card_colors(store, n)
                return out
            if prop == "Produce" and valid.startswith("Land.") and "OppCtrl" in valid:
                return _opponent_land_colours(gs, player, store)
            if prop == "Is":
                out = set()
                for e in player.battlefield:
                    if entry_matches(store, e, valid, is_self=(e is entry)):
                        out |= card_colors(store, e["name"], e.get("face", 0))
                return out
            raise ForgeUnsupported("reflected mana")
        ctx = fr.ManaContext(identity=identity, artifacts=count_artifacts(player, store),
                             counters={k.upper(): v for k, v in (entry.get("counters") or {}).items()},
                             reflect_colors=reflect if allow_reflect else None)
        try:
            found = fr.mana_abilities(ff, ctx)
            if not found.manual:
                return found.options, found.manual, found.inactive, found.hand
            # The script has a mana ability we can't read. The card text might still be automatable.
            text_options, text_manual = mana_options(info_of(store, name), face, identity)
            if text_options and not text_manual:
                return list(text_options), [], found.inactive, found.hand
            return found.options, found.manual, found.inactive, found.hand
        except _FORGE_ERRORS:
            pass
    options, manual = mana_options(info_of(store, name), face, identity)
    return list(options), list(manual), [], []


def _tap_targets(gs, player, entry, expr, store, for_planning, strict=True):
    """Battlefield indexes of permanents that could be tapped for 'tap an untapped X you control'.

    When planning with strict=True, permanents that can make mana on their own are not offered
    (tapping them as a cost would waste their mana); strict=False offers them too.
    """
    _count, valid = expr
    out = []
    for i, e in enumerate(player.battlefield):
        if e is entry or e.get("tapped") or e.get("awaiting"):
            continue
        try:
            if not entry_matches(store, e, valid):
                continue
        except ForgeUnsupported:
            continue
        if for_planning and strict:      # don't spend a permanent that can make mana on its own
            if tap_options(gs, player, e, store, for_planning=True, use_tap_other=False)[0]:
                continue
        out.append(i)
    return out


def tap_options(gs, player, entry, store, for_planning=False, use_tap_other=True, strict_targets=True):
    """What can this permanent do for mana right now?

    Returns (options, manual_lines, blocked_reason). `options` already include
    Kinnan-style bonus mana. With for_planning=True, options whose mana has a
    spending restriction are left out, because the auto-payer can't honour it.
    Options that tap another permanent as a cost (Springleaf Drum) are offered once per
    possible permanent when for_planning is False; the auto-payer picks one itself.
    """
    info = info_of(store, entry["name"])
    if not info:
        return [], [], "card data is still loading"
    if entry.get("awaiting"):
        return [], [], "it is waiting for your choice (click it to choose)"
    if entry.get("tapped"):
        return [], [], "already tapped"
    face = entry.get("face", 0)
    ff = _forge(store, entry["name"], face)
    creature = "Creature" in _face_text(info, face, "type_line")
    haste = fr.has_haste(ff) if ff is not None else _has_haste(_face_text(info, face))
    if creature and entry.get("summoning_sick") and not haste:
        return [], [], "summoning sick"

    raw, manual, inactive, _hand = _mana_abilities(gs, player, entry, store)
    bonuses = mana_bonuses(player, store, entry)
    result, blocked = [], ""
    for opt in raw:
        if opt.condition == "metalcraft" and count_artifacts(player, store) < 3:
            blocked = blocked or "metalcraft isn't active (you need three artifacts)"
            continue
        if for_planning and (opt.restrict or opt.note.startswith("Spend this mana only")):
            continue
        variants = [opt]
        if opt.tap_other:
            if not use_tap_other:
                continue
            targets = _tap_targets(gs, player, entry, opt.tap_other, store, for_planning, strict_targets)
            if not targets:
                blocked = blocked or "there is nothing untapped it could tap as a cost"
                continue
            if not for_planning:
                variants = [replace(opt, tap_target=i, tap_target_name=player.battlefield[i]["name"]) for i in targets]
        for v in variants:
            for colour_only in bonuses:
                v = fr.apply_bonus(v, 1, colour_only)
            result.append(v)
    reason = ""
    if not result and not manual:
        if blocked:
            reason = blocked
        elif inactive:
            reason = f"it can't make mana right now ({inactive[0]})"
    return result, list(manual), reason


def _apply_tap(gs, player, entry, option, target=None):
    entry["tapped"] = True
    if target is None and option.tap_target >= 0 and option.tap_target < len(player.battlefield):
        target = player.battlefield[option.tap_target]
    if option.tap_other and target is not None:
        target["tapped"] = True
    for color, n in option.produces.items():
        player.mana_pool.add(color, n)
    player.life -= option.life_cost + option.damage
    if option.sacrifice:
        for i, e in enumerate(player.battlefield):
            if e is entry:
                player.battlefield.pop(i)
                player.graveyard.append(entry["name"])
                break
    extra = f" (tapping {target['name']})" if option.tap_other and target is not None else ""
    gs._log(f"{player.name} taps {entry['name']} for {option.label()}{extra}")


def tap_for_mana(gs, player, entry, store, option_index=None):
    """Tap one permanent for mana. Returns (status, message, options).

    status is 'done', 'fail', or 'choose' (several options: call again with option_index).
    """
    opts, manual, reason = tap_options(gs, player, entry, store)
    if reason:
        return "fail", f"{entry['name']}: {reason}.", []
    if not opts:
        if manual:
            return "fail", (f"{entry['name']}: mana ability isn't automated ({manual[0]}). "
                            "Add the mana yourself with keys 1-6 and tap the card with right-click > Tap / untap."), []
        return "fail", f"{entry['name']} has no mana ability.", []
    if option_index is None:
        if len(opts) > 1:
            return "choose", "", opts
        option_index = 0
    _apply_tap(gs, player, entry, opts[option_index])
    return "done", f"Tapped {entry['name']} for {opts[option_index].label()}", opts


def hand_mana_options(gs, player, index, store):
    """Mana a card in hand can make from the hand (Elvish Spirit Guide: exile it, add {G})."""
    if not 0 <= index < len(player.hand):
        return []
    entry = {"name": player.hand[index], "face": 0}
    return list(_mana_abilities(gs, player, entry, store)[3])


def hand_mana(gs, player, index, store, option_index=0):
    """Use a card in hand for mana: exile it and add the mana to the pool. Returns (ok, message)."""
    opts = hand_mana_options(gs, player, index, store)
    if not opts or not 0 <= option_index < len(opts):
        return False, "That card has no mana ability usable from your hand."
    opt, name = opts[option_index], player.hand.pop(index)
    player.exile.append(name)
    for color, n in opt.produces.items():
        player.mana_pool.add(color, n)
    msg = f"{player.name} exiles {name} from hand for {opt.label()}"
    gs._log(msg)
    return True, msg


def add_mana(gs, player, color, amount=1):
    player.mana_pool.add(color, amount)
    gs._log(f"{player.name} adds {{{color}}} x{amount} to the mana pool (manual)")


def empty_mana_pool(player):
    player.mana_pool.clear()


def _assign_targets(gs, player, plan, store, strict=True):
    """For each planned tap that needs 'tap another permanent', pick a permanent. None if impossible.

    A permanent that is itself tapped for mana in the plan can't also be the target.
    """
    used = {id(e) for e, _ in plan}
    result = []
    for entry, opt in plan:
        if not opt.tap_other:
            result.append(None)
            continue
        options = [player.battlefield[i] for i in _tap_targets(gs, player, entry, opt.tap_other, store, True, strict)]
        pick = next((e for e in options if id(e) not in used), None)
        if pick is None:
            return None
        used.add(id(pick))
        result.append(pick)
    return result


def pay_mana_cost(gs, player, cost, store):
    """Pay `cost` from the pool, auto-tapping untapped permanents if the pool is short.

    Returns (ok, message). Nothing changes if the cost can't be paid.
    """
    cost = {k: v for k, v in cost.items() if k != "X"}
    pool = player.mana_pool
    if pool.can_pay(cost):
        pool.pay(cost)
        return True, ""

    # Three attempts: rocks that tap another permanent using only permanents that can't make mana
    # themselves; then also allowing mana creatures to be the tapped cost; then without such rocks.
    plan = targets = None
    for use_tap_other, strict in ((True, True), (True, False), (False, True)):
        sources = []
        for i, e in enumerate(player.battlefield):
            opts, _, _ = tap_options(gs, player, e, store, for_planning=True,
                                     use_tap_other=use_tap_other, strict_targets=strict)
            if opts:
                sources.append((i, bool(e.get("is_land")), opts))
        ok, chosen = plan_payment(cost, dict(pool.pool), sources)
        if not ok:
            plan = None
            continue
        by_index = {i: opts for i, _, opts in sources}
        plan = [(player.battlefield[i], by_index[i][oi]) for i, oi in chosen]   # entry objects, safe if one is sacrificed
        targets = _assign_targets(gs, player, plan, store, strict)
        if targets is not None:
            break
        plan = None                       # not enough permanents to tap as costs: plan again
    if plan is None or targets is None:
        return False, (f"Can't pay {cost_text(cost)}: floating {pool.text() or 'nothing'}, "
                       "and the untapped mana sources can't cover it.")

    for (entry, opt), target in zip(plan, targets):
        _apply_tap(gs, player, entry, opt, target)
    pool.pay(cost)
    return True, ""


# ---------------------------------------------------------------------------
# playing and casting
# ---------------------------------------------------------------------------
def _is_permanent(type_line):
    return any(t in type_line for t in PERMANENT_TYPES)


def play_from_hand(gs, player, index, store, face=0, tapped=None, free=False):
    """Play a land or cast a spell from hand. Returns (ok, message).

    Lands use the land drop (one per turn; free=True bypasses it). Spells are
    paid for automatically. Permanent spells go to the battlefield; instants and
    sorceries go to the graveyard, and their effects are resolved by hand.
    """
    if not 0 <= index < len(player.hand):
        return False, "That card is no longer in hand."
    name = player.hand[index]
    info = info_of(store, name)
    if not info:
        return False, f"Card data for {name} is still loading; try again in a moment."
    fi = card_face(info, face)
    type_line = fi.get("type_line") or ""

    if "Land" in type_line:
        if player.lands_played_this_turn >= 1 and not free:
            return False, "You already played a land this turn (right-click > Put onto battlefield to override)."
        player.hand.pop(index)
        entry, note = enter_battlefield(gs, player, name, store, tapped=tapped, face=face)
        if not free:
            player.lands_played_this_turn += 1
        shown = name if face == 0 else f"{fi.get('name', name)} (back face)"
        tapped_now = entry["tapped"] and not entry.get("awaiting")
        msg = f"{player.name} plays {shown}" + (" tapped" if tapped_now else "") + (f" ({note})" if note else "")
        gs._log(msg)
        return True, msg

    cost = parse_mana_cost(fi.get("mana_cost"))
    x_note = " (X = 0)" if "X" in cost else ""
    if not free:
        ok, why = pay_mana_cost(gs, player, cost, store)
        if not ok:
            return False, why

    player.hand.pop(index)
    shown = name if face == 0 else fi.get("name", name)
    if _is_permanent(type_line):
        enter_battlefield(gs, player, name, store, tapped=tapped, face=face)
        msg = f"{player.name} casts {shown}{x_note}"
    else:
        player.graveyard.append(name)
        msg = f"{player.name} casts {shown}{x_note}; it goes to the graveyard, resolve its effect by hand"
    if free:
        msg += " (no mana paid)"
    gs._log(msg)
    return True, msg


def cast_commander(gs, player, index, store, free=False):
    """Cast a commander from the command zone, paying commander tax ({2} per earlier cast)."""
    if not 0 <= index < len(player.command_zone):
        return False, "No commander there."
    name = player.command_zone[index]
    info = info_of(store, name)
    if not info:
        return False, f"Card data for {name} is still loading; try again in a moment."
    casts = player.commander_casts.get(name, 0)
    cost = parse_mana_cost(card_face(info, 0).get("mana_cost"))
    cost["generic"] = cost.get("generic", 0) + 2 * casts
    if not free:
        ok, why = pay_mana_cost(gs, player, cost, store)
        if not ok:
            return False, why
    player.command_zone.pop(index)
    player.commander_casts[name] = casts + 1
    enter_battlefield(gs, player, name, store)
    msg = f"{player.name} casts commander {name}" + (f" (tax {{{2 * casts}}})" if casts else "")
    gs._log(msg)
    return True, msg


def toggle_tapped(gs, player, entry):
    entry["tapped"] = not entry["tapped"]
    gs._log(f"{player.name} {'taps' if entry['tapped'] else 'untaps'} {entry['name']}")
    return True, f"{entry['name']} is now {'tapped' if entry['tapped'] else 'untapped'}"


# ---------------------------------------------------------------------------
# fetchlands
# ---------------------------------------------------------------------------
_FETCH_RE = re.compile(
    r"\{T\}(?:, Pay (\d+) life)?, Sacrifice this \w+: Search your library for an? (.+?) card"
    r"(?:, put it onto the battlefield( tapped)?)?")


def fetch_spec(store, entry):
    """If this permanent is a fetchland, return its spec, else None.

    The spec is {'life', 'types', 'tapped', 'what'} plus 'expr' (Forge's ChangeType expression)
    when it came from a Forge script. 'what' is a readable description ('Mountain or Forest').
    """
    info = info_of(store, entry["name"])
    if not info:
        return None
    ff = _forge(store, entry["name"], entry.get("face", 0))
    if ff is not None:
        try:
            return fr.fetch_spec(ff)          # a script is authoritative, including "not a fetchland"
        except _FORGE_ERRORS:
            pass
    m = _FETCH_RE.search(_face_text(info, entry.get("face", 0)))
    if not m:
        return None
    what = m.group(2)
    types = ["basic land"] if what.startswith("basic land") else re.split(r", | or ", what)
    return {"life": int(m.group(1) or 0), "types": types, "tapped": bool(m.group(3)),
            "what": "basic land" if types == ["basic land"] else " or ".join(types)}


def fetch_candidates(player, spec, store):
    """Indexes in the library of cards the fetchland can find (front faces only, as in the library)."""
    expr = spec.get("expr")
    result = []
    for i, name in enumerate(player.library):
        tl = type_line_of(store, name)
        if expr:
            try:
                if matches_valid(expr, type_words(tl), card_colors(store, name)):
                    result.append(i)
                continue
            except ForgeUnsupported:
                pass
        if "Land" not in tl:
            continue
        if spec["types"] == ["basic land"] or expr == "Land.Basic":
            if "Basic" in tl:
                result.append(i)
        elif any(t in tl for t in spec["types"]):
            result.append(i)
    return result


def crack_fetch(gs, player, entry, store):
    """Pay the fetchland's life and sacrifice it. Returns (ok, message, spec)."""
    spec = fetch_spec(store, entry)
    if not spec:
        return False, f"{entry['name']} is not a fetchland.", None
    if entry.get("tapped"):
        return False, f"{entry['name']} is tapped.", None
    for i, e in enumerate(player.battlefield):
        if e is entry:
            player.battlefield.pop(i)
            break
    player.graveyard.append(entry["name"])
    player.life -= spec["life"]
    gs._log(f"{player.name} cracks {entry['name']}" + (f" (pays {spec['life']} life)" if spec["life"] else ""))
    paid = f" Paid {spec['life']} life (now {player.life})." if spec["life"] else ""
    return True, f"Cracked {entry['name']}.{paid} Choose a {spec['what']} from your library.", spec


def finish_fetch(gs, player, library_index, spec, store):
    """Put the chosen land onto the battlefield and shuffle."""
    ok, msg = move_card(gs, player, "library", library_index, "battlefield", store,
                        tapped=True if spec.get("tapped") else None)
    shuffle_library(gs, player)
    return ok, msg


# ---------------------------------------------------------------------------
# mulligans
# ---------------------------------------------------------------------------
def cards_to_bottom(player):
    return max(0, player.mulligans - FREE_MULLIGANS)


def take_mulligan(gs, player):
    """London mulligan: shuffle the hand away and draw seven new cards."""
    player.mulligans += 1
    player.library.extend(player.hand)
    player.hand = []
    random.shuffle(player.library)
    player.draw(HAND_SIZE)
    n = cards_to_bottom(player)
    gs._log(f"{player.name} mulligans (#{player.mulligans}); will put {n} on the bottom after keeping.")
    return n


def bottom_cards(gs, player, hand_indexes):
    """Put the chosen hand cards on the bottom of the library (after keeping)."""
    for i in sorted(hand_indexes, reverse=True):
        player.library.append(player.hand.pop(i))
    gs._log(f"{player.name} keeps {len(player.hand)} cards, {len(hand_indexes)} put on the bottom.")


# ---------------------------------------------------------------------------
# turns
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# before the first turn: opening-hand effects and the seating order
# ---------------------------------------------------------------------------
def _opening_effects(store, name, face=0):
    """Opening-hand effects for one card: Forge script first, Oracle text as the fallback."""
    ff = _forge(store, name, face)
    if ff is not None:
        try:
            return fr.opening_hand_effects(ff)
        except ForgeUnsupported:
            return [{"name": "manual", "not_first": False, "counter": None, "exile": 0, "manual": True}]
    info = info_of(store, name)
    text = _face_text(info, face) if info else ""
    if "opening hand" not in text:
        return []
    if "begin the game with" not in text or "battlefield" not in text:
        return [{"name": "manual", "not_first": False, "counter": None, "exile": 0, "manual": True}]
    counter = re.search(r"(\w+) counter on it", text)
    return [{"name": "text", "not_first": "not the starting player" in text,
             "counter": (counter.group(1).upper(), 1) if counter else None,
             "exile": 1 if re.search(r"exile a card from your hand", text) else 0}]


def opening_hand_options(gs, player, store):
    """Cards in the opening hand that may begin the game on the battlefield.

    Returns [{"hand_index", "name", "counter", "exile", "label"}] and skips effects that are
    barred for the starting player (Gemstone Caverns). Cards whose effect the simulator can't
    model come back with "manual": True so the GUI can say so.
    """
    is_first = gs.players.index(player) == getattr(gs, "first_player_index", 0)
    out = []
    for i, name in enumerate(player.hand):
        for eff in _opening_effects(store, name):
            if eff.get("not_first") and is_first:
                continue
            label = f"Begin the game with {name} on the battlefield"
            if eff.get("counter"):
                label += f" with a {eff['counter'][0].lower()} counter"
            if eff.get("exile"):
                label += f", then exile {eff['exile']} card{'s' if eff['exile'] != 1 else ''} from your hand"
            out.append({"hand_index": i, "name": name, "counter": eff.get("counter"), "exile": eff.get("exile", 0),
                        "manual": bool(eff.get("manual")), "label": label})
            break
    return out


def begin_with_on_battlefield(gs, player, hand_index, store, exile_indexes=()):
    """Use an opening-hand effect. exile_indexes are hand positions (in the hand as it is now,
    including the card itself) to exile afterwards. Returns (ok, message)."""
    if not (0 <= hand_index < len(player.hand)):
        return False, "That card is no longer in your hand."
    name = player.hand[hand_index]
    opts = [o for o in opening_hand_options(gs, player, store) if o["hand_index"] == hand_index]
    if not opts or opts[0]["manual"]:
        return False, f"{name} has nothing the simulator can do from the opening hand."
    opt = opts[0]
    exile_indexes = sorted(set(exile_indexes))
    needed = min(opt["exile"], len(player.hand) - 1)        # nothing to exile if the card was the only one
    if len(exile_indexes) != needed or hand_index in exile_indexes or \
            any(not (0 <= i < len(player.hand)) for i in exile_indexes):
        return False, f"Pick {needed} other card(s) from your hand to exile."
    exiled = [player.hand[i] for i in exile_indexes]
    for i in sorted(exile_indexes + [hand_index], reverse=True):
        player.hand.pop(i)
    entry, _note = enter_battlefield(gs, player, name, store, tapped=False)
    entry["summoning_sick"] = False
    if opt["counter"]:
        entry["counters"][opt["counter"][0]] = entry["counters"].get(opt["counter"][0], 0) + opt["counter"][1]
    player.exile.extend(exiled)
    msg = f"{player.name} begins the game with {name} on the battlefield"
    if opt["counter"]:
        msg += f" with a {opt['counter'][0].lower()} counter"
    if exiled:
        msg += "; exiled " + ", ".join(exiled) + " from hand"
    gs._log(msg + ".")
    return True, msg + "."


def begin_game(gs, store, me_index=0):
    """Start the game after mulligans. Players seated before the human (when someone else goes
    first) take their first turns, then the human's first turn begins."""
    first = getattr(gs, "first_player_index", 0)
    n = len(gs.players)
    gs.turn_number = 1
    gs._log(f"{gs.players[first].name} goes first.")
    idx = first
    while idx != me_index:
        start_turn(gs, store, idx)
        ai_take_turn(gs, store, idx)
        gs.players[idx].mana_pool.clear()
        idx = (idx + 1) % n
    start_turn(gs, store, me_index)


def start_turn(gs, store, index):
    """Begin a player's turn: untap, reset the land drop, draw."""
    player = gs.players[index]
    gs.active_player_index = index
    gs.phase = "main1"
    player.lands_played_this_turn = 0
    player.mana_pool.clear()
    for e in player.battlefield:
        info = info_of(store, e["name"])
        ff = _forge(store, e["name"], e.get("face", 0))
        stays = (fr.doesnt_untap(ff) if ff is not None else
                 bool(info and re.search(r"doesn.t untap during your untap step", _face_text(info, e.get("face", 0)))))
        if stays:
            if e["tapped"]:
                gs._log(f"{e['name']} stays tapped (doesn't untap normally; untap it by hand if you pay for it).")
        else:
            e["tapped"] = False
        e["summoning_sick"] = False
    gs._log(f"{player.name}'s turn begins.")
    skip_draw = index == getattr(gs, "first_player_index", 0) and not getattr(gs, "first_turn_taken", False)
    gs.first_turn_taken = True
    if not skip_draw:                                 # the starting player skips the draw on the game's first turn
        drawn = player.draw(1)
        gs._log(f"{player.name} draws a card." if drawn else f"{player.name} has no cards to draw!")


def ai_take_turn(gs, store, index):
    """Placeholder opponent turn: untap and draw, nothing else (no AI is wired in yet)."""
    gs._log(f"{gs.players[index].name} takes no actions (no AI yet).")


def end_turn(gs, store, me_index=0):
    """End the human's turn, let the other players take theirs, and begin the human's next turn."""
    me = gs.players[me_index]
    me.mana_pool.clear()
    if len(me.hand) > HAND_SIZE:
        gs._log(f"{me.name} has {len(me.hand)} cards in hand: discard down to {HAND_SIZE} by hand.")
    for offset in range(1, len(gs.players)):
        idx = (me_index + offset) % len(gs.players)
        start_turn(gs, store, idx)
        ai_take_turn(gs, store, idx)
        gs.players[idx].mana_pool.clear()
    gs.turn_number += 1
    start_turn(gs, store, me_index)


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------
def draw_cards(gs, player, n=1):
    """Draw n cards. Returns (ok, message)."""
    drawn = player.draw(n)
    if not drawn:
        return False, f"{player.name}'s library is empty."
    gs._log(f"{player.name} draws {len(drawn)} card{'s' if len(drawn) != 1 else ''}.")
    return True, f"Drew {', '.join(drawn)}"
