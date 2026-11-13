# SPDX-License-Identifier: GPL-3.0-or-later
"""
start_effects.py - one seat's start effects (Round PROG3). No pygame.

The Adventure's levels and perks (and, from PROG4, gear) change how the player's seat starts a duel: starting life, the opening
hand's size, cards on the battlefield at the start, a card in the command zone. Only the player's seat ever gets them; a foe never
does. Karl, 2 Nov 2026 (claude/HANDOVER_PROGRESSION_2026-11-02.md, answer 1): "Duel too, freely" - this lifts his 9 Oct rule that
every Adventure duel starts at 20 life.

The model is house rules (house_rules.py) - the same three places:
  a dict here            {"life": 23, "hand": 8, "battlefield": ["token:c_a_treasure_sac"], "command": []}
  Forge's .dck file      "Manticore Start=life:23;hand:8;battlefield:token:c_a_treasure_sac" in the PLAYER's [metadata] only. A
                         resumed game, Undo's rebuild and a bug report's replay start from the saved .dck files, so they get the same
                         start (forge_client reads it).
  the engine             java_bridge Main --start-effects SPEC: StartEffects.java fills Forge's own RegisteredPlayer fields
                         (setStartingLife, setStartingHand, addExtraCardsOnBattlefield, addExtraCardsInCommandZone - Forge's
                         Adventure duels use the same ones), which Forge applies when the game starts, before the opening hands.
                         "ready" says "startEffects" ({} for none); a bridge without the key is older than this program - a fatal
                         error, never a quiet duel without the effects (forge_client).

SPEC: "key:value" items joined by ";" (no card name has a ";"; a value may hold ":" - "Circle of Protection: Red").
"""
import re

KEYS = ("life", "hand", "battlefield", "command")
LIFE_RANGE = (1, 999)
HAND_RANGE = (0, 20)
TOKEN = "token:"                    # battlefield:token:c_a_treasure_sac = one of Forge's token scripts (res/tokenscripts)
TREASURE = TOKEN + "c_a_treasure_sac"

_DCK_KEY = "Manticore Start"
_DCK_LINE = re.compile(r"^\s*Manticore Start\s*=\s*(.*?)\s*$", re.I | re.M)


class StartEffectsError(ValueError):
    pass


def _whole(v, key, lo, hi):
    if isinstance(v, bool):
        raise StartEffectsError(f"{key}: {v!r} is not a whole number")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise StartEffectsError(f"{key}: {v!r} is not a whole number")
    if str(n) != str(v).strip() and not isinstance(v, int):
        raise StartEffectsError(f"{key}: {v!r} is not a whole number")
    if not lo <= n <= hi:
        raise StartEffectsError(f"{key}: {n} is not from {lo} to {hi}")
    return n


def normal(effects):
    """The effects as a plain dict with only what is asked for ({} = none): "life"/"hand" ints, "battlefield"/"command" lists of
    names in order. Raises StartEffectsError for a key or value the bridge would refuse."""
    if not effects:
        return {}
    if not isinstance(effects, dict):
        raise StartEffectsError(f"start effects must be a table, not {type(effects).__name__}")
    out = {}
    for k, v in effects.items():
        if k not in KEYS:
            raise StartEffectsError(f"unknown start effect {k!r} (known: {', '.join(KEYS)})")
        if k == "life" and v is not None:
            out["life"] = _whole(v, "life", *LIFE_RANGE)
        elif k == "hand" and v is not None:
            out["hand"] = _whole(v, "hand", *HAND_RANGE)
        elif k in ("battlefield", "command") and v:
            names = [v] if isinstance(v, str) else list(v)
            clean = []
            for n in names:
                if not isinstance(n, str) or not n.strip() or ";" in n:
                    raise StartEffectsError(f"{k}: {n!r} is not a card name")
                clean.append(n.strip())
            out[k] = clean
    return out


def spec(effects):
    """The bridge's SPEC for these effects ("" for none)."""
    e = normal(effects)
    items = []
    if "life" in e:
        items.append(f"life:{e['life']}")
    if "hand" in e:
        items.append(f"hand:{e['hand']}")
    for k in ("battlefield", "command"):
        items += [f"{k}:{n}" for n in e.get(k, ())]
    return ";".join(items)


def parse(text):
    """SPEC -> the dict (StartEffectsError for anything the bridge would refuse). The bridge's StartEffects.parse reads it the same."""
    out = {}
    for item in (text or "").split(";"):
        item = item.strip()
        if not item:
            continue
        key, sep, val = item.partition(":")
        key, val = key.strip().lower(), val.strip()
        if not sep or not key:
            raise StartEffectsError(f"start effect {item!r} is not key:value")
        if key in ("life", "hand"):
            out[key] = val
        elif key in ("battlefield", "command"):
            if not val:
                raise StartEffectsError(f"{key}: no card named")
            out.setdefault(key, []).append(val)
        else:
            raise StartEffectsError(f"unknown start effect {key!r} (known: {', '.join(KEYS)})")
    return normal(out)


# ---- Forge's .dck file ----------------------------------------------------------------------------------------------------------
def dck_lines(effects):
    """Extra [metadata] lines for the player's .dck: ["Manticore Start=..."], or [] (the file exactly as before)."""
    s = spec(effects)
    return [f"{_DCK_KEY}={s}"] if s else []


def from_dck(text):
    """The effects a .dck's [metadata] names ({} for none, or a line this copy can't read - said by the caller's game, which
    then starts without them only if the bridge also refuses; a damaged line is a ValueError here)."""
    m = _DCK_LINE.search(text or "")
    return parse(m.group(1)) if m else {}


def from_dck_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_dck(f.read())
    except (OSError, UnicodeDecodeError, TypeError):
        return {}


# ---- the engine and the screens -------------------------------------------------------------------------------------------------
def engine_args(effects):
    """The bridge's arguments: ["--start-effects", SPEC], or [] for none (the command line exactly as it was)."""
    s = spec(effects)
    return ["--start-effects", s] if s else []


def same(asked, reported):
    """Did the bridge's "ready" line report what was asked? (reported is its "startEffects" object; None = an older bridge)."""
    if reported is None:
        return not normal(asked)
    try:
        return normal(asked) == normal(reported)
    except StartEffectsError:
        return False


TOKEN_WORDS = {TREASURE: "a Treasure", TOKEN + "c_a_clue_draw": "a Clue",           # Round PROG4: the tokens gear gives
               TOKEN + "w_1_1_soldier": "a 1/1 Soldier"}


def card_words(name):
    """"a Treasure" for token:c_a_treasure_sac (TOKEN_WORDS for the others named), else the card's name."""
    if name.lower() in TOKEN_WORDS:
        return TOKEN_WORDS[name.lower()]
    if name.lower().startswith(TOKEN):
        return "a " + name[len(TOKEN):].replace("_", " ") + " token"
    return name


def describe(effects, base_life=20, base_hand=7):
    """One short line for the encounter screen: "You start with 23 life, 8 cards in hand and a Treasure." ("" when nothing
    differs from the plain duel)."""
    e = normal(effects)
    parts = []
    if e.get("life", base_life) != base_life:
        parts.append(f"{e['life']} life")
    if e.get("hand", base_hand) != base_hand:
        parts.append(f"{e['hand']} cards in hand")
    for n in e.get("battlefield", ()):
        parts.append(card_words(n))
    for n in e.get("command", ()):
        parts.append(f"{card_words(n)} in your command zone")
    if not parts:
        return ""
    words = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"You start with {words}."
