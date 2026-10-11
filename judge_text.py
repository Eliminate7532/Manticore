# SPDX-License-Identifier: GPL-3.0-or-later
"""Round JDG1: the words of the Judge?! window - only official text, nothing made up.

page(...) returns a list of blocks for judge_dialog to draw:
  ("title", text)                     the card's name
  ("sub", text)                       one line under it (where a stack item sits, who put it there)
  ("head", text)                      a section heading
  ("face", text)                      a face's name and mana cost
  ("type", text)                      a type line
  ("text", text)                      rules text (one paragraph; {G}-style symbols are drawn as symbols)
  ("pt", text)                        power/toughness or loyalty
  ("note", text)                      a status line (not loaded, offline, a token ...)
  ("ruling", date, source, comment)   one official ruling
  ("credit", text)                    where the words come from
No pygame here.
"""
import rulings as rul

SOURCE_WORDS = {"wotc": "Wizards", "scryfall": "Scryfall note"}
STATUS_WORDS = {
    rul.LOADING: "Rulings not loaded yet.",
    rul.OFFLINE: "Rulings not cached (offline).",
    rul.FAILED: "Rulings couldn't be loaded from Scryfall.",
}
CREDIT = ("Card text and rulings: Scryfall. Rulings marked Wizards are Wizards of the Coast's own; "
          "a Scryfall note is Scryfall's.")


def ordinal(n):
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def stack_line(index, count):
    """index 0 = the top of the stack, which resolves first."""
    if count <= 1:
        return "On the stack: the only item - it resolves next."
    return f"On the stack: resolves {ordinal(index + 1)} of {count}" + (" (next)." if index == 0 else ".")


def _paragraphs(text):
    out = []
    for part in str(text or "").replace("\r\n", "\n").split("\n"):
        part = part.strip()
        if part:
            out.append(("text", part))
    return out


def _pt(face):
    if face.get("power") is not None and face.get("toughness") is not None:
        return [("pt", f"{face['power']}/{face['toughness']}")]
    if face.get("loyalty") is not None:
        return [("pt", f"Loyalty {face['loyalty']}")]
    if face.get("defense") is not None:
        return [("pt", f"Defense {face['defense']}")]
    return []


def _face(face):
    cost = face.get("mana_cost") or ""
    out = [("face", (face.get("name") or "") + (f"   {cost}" if cost else ""))]
    if face.get("type_line"):
        out.append(("type", face["type_line"]))
    return out + _paragraphs(face.get("oracle_text")) + _pt(face)


def oracle_blocks(scry):
    """Every face of a Scryfall card: name and cost, type line, Oracle text, P/T or loyalty."""
    faces = scry.get("card_faces") or []
    if faces:
        out = []
        for f in faces:
            out += _face(f)
        return out
    return _face(scry)


def forge_blocks(card):
    """The table's own copy of the card (Forge's text) when Scryfall's isn't there: a token, or data not loaded yet."""
    cost = card.get("cost") or ""
    out = [("face", (card.get("name") or "?") + (f"   {cost}" if cost else ""))]
    if card.get("type"):
        out.append(("type", card["type"]))
    out += _paragraphs(card.get("text"))
    if card.get("power") is not None and card.get("toughness") is not None and card.get("isCreature"):
        out.append(("pt", f"{card['power']}/{card['toughness']}"))
    return out


def page(card, scry=None, status=rul.NO_CARD, rulings=(), stack=None, who="", targets=(), offline=False):
    """The blocks for one card. card: the snapshot's card; scry: Scryfall's card object (None when not loaded); status/rulings:
    rulings.get(); stack: (index, count, item) when the card is there as a stack item; who: who put it there ("You", "AI 1");
    targets: their names."""
    card = card or {}
    title = (scry or {}).get("name") or card.get("name") or "?"
    out = [("title", title)]
    if stack is not None:
        index, count, item = stack
        kind = "trigger" if item.get("trigger") else "ability" if item.get("ability") else "spell"
        out.append(("sub", stack_line(index, count)))
        out.append(("sub", f"Put there by: {who or '?'} ({kind})"))
        if targets:
            out.append(("sub", "Targets: " + ", ".join(targets)))
        if kind != "spell":
            words = str(item.get("text") or "").split(" [")[0].strip()
            if words and words != card.get("name"):
                out.append(("head", "This " + kind))
                out += _paragraphs(words)
    token = bool(card.get("token"))
    if scry:
        out.append(("head", "Oracle text"))
        out += oracle_blocks(scry)
    else:
        if token:
            out.append(("head", "Card text (a token - Forge's text)"))
        elif offline:
            out.append(("head", "Card text (Forge's - Scryfall's Oracle text isn't cached, offline)"))
        else:
            out.append(("head", "Card text (Forge's - Scryfall's Oracle text is loading)"))
        out += forge_blocks(card)
    out.append(("head", "Rulings" + (f" ({len(rulings)})" if status == rul.OK and rulings else "")))
    if status == rul.OK:
        if rulings:
            for r in rulings:
                out.append(("ruling", str(r.get("published_at") or ""), SOURCE_WORDS.get(r.get("source"), r.get("source") or ""),
                            str(r.get("comment") or "")))
        else:
            out.append(("note", "No official rulings for this card."))
    elif status == rul.NO_CARD:
        if token and not scry:
            out.append(("note", "A token has no rulings of its own."))
        elif offline:
            out.append(("note", "Rulings not cached (offline)."))
        else:
            out.append(("note", "Rulings not loaded yet."))
    else:
        out.append(("note", STATUS_WORDS.get(status, "Rulings not loaded yet.")))
    out.append(("credit", CREDIT))
    return out
