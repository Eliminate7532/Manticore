# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_why.py - Forge's refusal messages, explained (Karl, 2 Oct: "When prompts like this come up I need them to explain why").

Forge refuses some answers with a one-line message written for a programmer, e.g. after OK on blockers:
    "Lathril, Blade of the Elves (201) cannot be blocked with 1 creatures you've assigned"
It never says WHY (Lathril has menace). explain() turns each known message into a plain title, the reason (read from the card's
keywords and rules text in the snapshot) and what to do. A message it doesn't know is shown as before, without the card number.

The messages come from Forge's CombatUtil.validateBlocks (forge-game, 3a74143) - the only refusals Forge's own Input code sends
through gui.message() during combat (InputBlock.onOk). Add new ones here as the soak or a game turns them up.

    explain(text, find_card) -> Explanation(title, body, card_id) or None
    find_card(id) -> the snapshot card dict (name, text, keywords) or None
"""
import re
from collections import namedtuple

Explanation = namedtuple("Explanation", "title body card_id")

TITLE_BLOCK = "Can't block like that"
_CARD = r"(?P<{0}>.+?) \((?P<{0}_id>\d{{1,6}})\)"

_NUMBER = {"two": 2, "three": 3, "four": 4, "five": 5}
_MIN_BY = re.compile(r"can't be blocked except by (two|three|four|five|\d+) or more creatures", re.I)
_MAX_ONE = re.compile(r"can't be blocked by more than one creature", re.I)
_MAX_N = re.compile(r"can't be blocked by more than (two|three|four|five|\d+) creatures", re.I)


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _words(n):
    return {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}.get(n, str(n))


def _num(word):
    return _NUMBER.get(word.lower(), None) if not word.isdigit() else int(word)


def _rule_line(card, pattern):
    """The line of the card's own rules text that matches `pattern`, quoted, or ''."""
    for line in (card or {}).get("text", "").splitlines():
        if re.search(pattern, line, re.I):
            return line.strip()
    return ""


def _limits(card):
    """(min, max, reason, quote) for how many creatures may block `card`, from its keywords and rules text; None if nothing found."""
    if not card:
        return None
    text = card.get("text") or ""
    keywords = [k.lower() for k in card.get("keywords") or []]
    if "menace" in keywords or re.search(r"\bmenace\b", text, re.I):          # before the "except by N or more" text: menace's reminder says it too
        return 2, None, "it has menace, so it can't be blocked except by two or more creatures", _rule_line(card, r"\bmenace\b")
    m = _MIN_BY.search(text)
    if m and _num(m.group(1)):
        n = _num(m.group(1))
        return n, None, "it can't be blocked except by %s or more creatures" % _words(n), _rule_line(card, _MIN_BY.pattern)
    if _MAX_ONE.search(text):
        return None, 1, "it can't be blocked by more than one creature", _rule_line(card, _MAX_ONE.pattern)
    m = _MAX_N.search(text)
    if m and _num(m.group(1)):
        n = _num(m.group(1))
        return None, n, "it can't be blocked by more than %s creatures" % _words(n), _rule_line(card, _MAX_N.pattern)
    return None


def _blocked_with(m, find_card):
    name, cid, n = m.group("a"), int(m.group("a_id")), int(m.group("n"))
    lim = _limits(find_card(cid))
    if lim is None:
        body = ("You assigned %s to block %s, and something stops it being blocked by that many - for example menace, or an "
                "effect that lets it be blocked by only one creature. Hover %s to read its abilities, then change how many "
                "creatures block it (or block nothing with it)." % (_plural(n, "creature"), name, name))
        return Explanation(TITLE_BLOCK, body, cid)
    low, high, reason, quote = lim
    if low and n < low:
        todo = "Add %s more blocker%s to %s, or take %s off it." % (
            _words(low - n), "" if low - n == 1 else "s", name, "that blocker" if n == 1 else "those blockers")
    elif high and n > high:
        todo = "Take blockers off %s until only %s block%s it." % (name, _words(high), "s" if high == 1 else "")
    else:
        todo = "Change how many creatures block %s." % name
    body = "%s can't be blocked by %s%s: %s.\n%s" % (name, "only " if low and n < low else "", _plural(n, "creature"), reason, todo)
    if quote:
        body += "\n\n%s: \"%s\"" % (name, quote)
    return Explanation(TITLE_BLOCK, body, cid)


def _must_still_block(m, find_card):
    b, a = m.group("b"), m.group("a")
    return Explanation(TITLE_BLOCK, "%s must block %s if it can - an effect requires it.\nAssign %s to block %s, then press OK."
                       % (b, a, b, a), int(m.group("b_id")))


def _must_block_attacker(m, find_card):
    b = m.group("b")
    which = "the attacker that requires it" if m.group("which") == "the right ones." else "an attacker"
    return Explanation(TITLE_BLOCK, "%s is forced to block (an attacker says all creatures able to block it must do so, or "
                       "another effect requires it).\nAssign %s to block %s, then press OK." % (b, b, which), int(m.group("b_id")))


def _blocks_each_combat(m, find_card):
    b = m.group("b")
    return Explanation(TITLE_BLOCK, "%s blocks each combat if able.\nAssign it to block an attacker, then press OK." % b,
                       int(m.group("b_id")))


def _cant_block_alone(m, find_card):
    b = m.group("b")
    return Explanation(TITLE_BLOCK, "%s can't block alone: at least one other creature of yours must block too.\nAssign "
                       "another blocker, or take %s off." % (b, b), int(m.group("b_id")))


def _two_others(m, find_card):
    b = m.group("b")
    return Explanation(TITLE_BLOCK, "%s can't block unless at least two other creatures of yours block too.\nAssign two "
                       "more blockers, or take %s off." % (b, b), int(m.group("b_id")))


def _greater_power(m, find_card):
    b = m.group("b")
    return Explanation(TITLE_BLOCK, "%s can't block unless a creature of yours with greater power also blocks.\nAssign a "
                       "stronger blocker as well, or take %s off." % (b, b), int(m.group("b_id")))


RULES = [
    (re.compile("^" + _CARD.format("a") + r" cannot be blocked with (?P<n>\d+) creatures you've assigned\.?$"), _blocked_with),
    (re.compile("^" + _CARD.format("b") + r" must still block " + _CARD.format("a") + r"\.$"), _must_still_block),
    (re.compile("^" + _CARD.format("b") + r" must block an attacker, but has not been assigned to block (?P<which>the right ones\.|any\.)$"),
     _must_block_attacker),
    (re.compile("^" + _CARD.format("b") + r" must block each combat but was not assigned to block any attacker now\.$"), _blocks_each_combat),
    (re.compile("^" + _CARD.format("b") + r" can't block alone\.$"), _cant_block_alone),
    (re.compile("^" + _CARD.format("b") + r" can't block unless at least two other creatures block\.$"), _two_others),
    (re.compile("^" + _CARD.format("b") + r" can't block unless a creature with greater power also blocks\.$"), _greater_power),
]


def explain(text, find_card=lambda _cid: None):
    """An Explanation for a Forge refusal message, or None when it isn't one we know."""
    t = " ".join((text or "").split())
    for rx, build in RULES:
        m = rx.match(t)
        if m:
            try:
                return build(m, find_card)
            except Exception:
                return None
    return None
