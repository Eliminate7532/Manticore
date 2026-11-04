# SPDX-License-Identifier: GPL-3.0-or-later
"""The deck's shape, beside Card quality in the deck editor (Round PROG1b; Karl, 3 Nov 2026: "A now" - the meter renamed Card
quality and the deck's shape added: Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md section 8). No pygame: rpg_screens draws.

    shape(cards, pool, removal)  {"curve": [n at mana value 0, 1, 2, 3, 4, 5, 6+] of the non-land cards, "creatures", "other"
                                 (non-land, non-creature), "lands", "removal"} - copies counted; a card the pool doesn't know is
                                 left out of every count
    is_removal(text)             THE removal rule, on a card's Oracle text (below)
    removal_names()              the cards the rule marks, from rpg/data/removal.json (tools/rpg_removal.py writes it from Forge's
                                 own card scripts: the RPG's card pool, rpg/data/cards.json, has no rules text)
    shape_lines(s)               the counts as words

THE REMOVAL RULE (Claude's, 3 Nov 2026; Karl's to change - one place, REMOVAL_PATTERNS). A card is removal when one sentence of its
Oracle text (any face you can cast; a transforming card's back face doesn't count) does one of these to a creature, a planeswalker
or another non-land permanent:
  1. destroys it: "destroy target ...", "destroy up to N target ...", "destroy all / each ... creature(s)"
  2. exiles it from the battlefield: "exile target ..." (not a card in a graveyard, hand or library)
  3. deals damage to a creature: "... deals N damage to any target / to target creature / divided among any number of target
     creatures / to each creature ..." (not "target player or planeswalker" alone)
  4. shrinks it: "... gets -N/-N" (or -X/-X), or "-N/-N counters on target / each ..." (not a -1/-1 counter on itself)
  5. makes it fight: "fights"
  6. makes a player sacrifice one: "... sacrifices a creature / a permanent / ..." (an edict; not "sacrifice" as your own cost)
  7. locks it: "enchanted creature can't attack or block" (Pacifism)
Not removal by this rule: returning to hand (bounce), tapping, counterspells, land destruction alone.
"""
import json
import os
import re

from rpg import data_path

REMOVAL_FILE = data_path("removal.json")
TARGETS = r"(?:creature|planeswalker|permanent|artifact|enchantment|battle)"
REMOVAL_PATTERNS = (
    ("destroy", re.compile(r"\bdestroy (?:up to \w+ )?(?:another )?(?:target|all|each)\b[^.]*?\b" + TARGETS)),
    ("exile", re.compile(r"\bexile (?:up to \w+ )?(?:another )?target\b[^.]*?\b" + TARGETS)),
    ("damage", re.compile(r"\bdeals?\b[^.]*?\bdamage\b[^.]*?\b(?:to|among)\b[^.]*?\b(?:any (?:other )?target|target\b[^.]*?"
                          r"\bcreature|each (?:other )?(?:\w+ )*?creature)")),
    ("shrink", re.compile(r"\bgets? -(?:\d+|x)/-(?:\d+|x)\b|-(?:\d+|x)/-(?:\d+|x) counters? on (?:\w+ )*?(?:target|each)\b")),
    ("fight", re.compile(r"\bfights?\b")),
    ("edict", re.compile(r"\bsacrifices (?:a|an|one|two|three|\w+) (?:\w+ )*?" + TARGETS)),
    ("lock", re.compile(r"\benchanted creature can't attack or block\b")),
)
NOT_FROM_BATTLEFIELD = re.compile(r"\b(?:graveyard|graveyards|hand|library|stack)\b")


def removal_reason(text):
    """Which of the rule's kinds this Oracle text is ("destroy", "exile", ...), or None. Each sentence is judged on its own."""
    t = (text or "").replace("\\n", "\n").lower()
    for sentence in re.split(r"[.\n]", t):
        for kind, pat in REMOVAL_PATTERNS:
            if not pat.search(sentence):
                continue
            if kind == "exile" and NOT_FROM_BATTLEFIELD.search(sentence):
                continue                          # "exile target card from a graveyard": not removal
            if kind == "exile" and "you control" in sentence and "return" in sentence:
                continue                          # "exile target creature you control, then return it": a flicker
            return kind
    return None


def is_removal(text):
    return removal_reason(text) is not None


_REMOVAL = {}


def removal_names(path=REMOVAL_FILE):
    """The set of card names (normal_name keys) the rule marks; empty (counted as 0) when the file is missing or broken - the
    shape must never stop the deck editor."""
    if path not in _REMOVAL:
        from rpg import cardpool
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            _REMOVAL[path] = {cardpool.normal_name(n) for n in d.get("cards", [])}
        except (OSError, ValueError, AttributeError):
            _REMOVAL[path] = set()
    return _REMOVAL[path]


def is_land(card):
    """The same land test as Card quality (rpg.progress.card_quality): the type line says Land."""
    return "Land" in (card.type_line or "")


CURVE_TOP = 6                                     # the last column is "6+"


def shape(cards, pool, removal=None):
    from rpg import cardpool
    removal = removal_names() if removal is None else removal
    out = {"curve": [0] * (CURVE_TOP + 1), "creatures": 0, "other": 0, "lands": 0, "removal": 0}
    for name, k in cards:
        card = pool.get(name) if pool is not None else None
        if card is None:
            continue
        k = int(k)
        if is_land(card):
            out["lands"] += k
            continue
        out["curve"][min(CURVE_TOP, max(0, int(card.mana_value or 0)))] += k
        if "Creature" in (card.type_line or ""):
            out["creatures"] += k
        else:
            out["other"] += k
        if cardpool.normal_name(card.name) in removal:
            out["removal"] += k
    return out


CURVE_LABELS = tuple(str(i) for i in range(CURVE_TOP)) + (f"{CURVE_TOP}+",)


def shape_lines(s):
    """The counts as the deck editor says them (Karl to edit)."""
    return [f"Creatures {s['creatures']}", f"Other spells {s['other']}", f"Lands {s['lands']}", f"Removal {s['removal']}"]


# ---- the build side: Forge's card scripts -> rpg/data/removal.json (tools/rpg_removal.py) ------------------------------------------

def script_faces(text):
    """[(name, oracle)] of a Forge card script's faces you can cast: every face, except a transforming card's back face."""
    parts = text.split("\nALTERNATE")
    mode = re.search(r"^AlternateMode:(\w+)", parts[0], re.M)
    faces = []
    for i, part in enumerate(parts):
        if i > 0 and mode and mode.group(1) == "DoubleFaced":
            continue
        name = re.search(r"^Name:(.*)$", part, re.M)
        oracle = re.search(r"^Oracle:(.*)$", part, re.M)
        if name:
            faces.append((name.group(1).strip(), oracle.group(1).strip() if oracle else ""))
    return faces


def scan_cardsfolder(folder):
    """{card name (as the script's first face names it): removal kind or None} for every script under `folder`."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.endswith(".txt"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            faces = script_faces(text)
            if not faces:
                continue
            kind = next((k for k in (removal_reason(o) for _n, o in faces) if k), None)
            names = [n for n, _o in faces]
            out[names[0]] = kind
            if len(faces) > 1:
                out[" // ".join(names)] = kind
    return out
