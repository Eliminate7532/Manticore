# SPDX-License-Identifier: GPL-3.0-or-later

"""
deck_importer.py - Import decklists from Moxfield, Archidekt, or pasted text
"""
import json
import os
import re
from urllib.parse import urlencode

import requests

import paths


class DeckImportError(Exception):
    pass


# Descriptive User-Agent for sites that ask API users to identify themselves.
APP_HEADERS = {
    "User-Agent": "CommanderSim/0.1 (personal hobby project)",
    "Accept": "application/json",
}


def _extract_moxfield_id(url_or_id):
    """Pull the public deck ID out of a Moxfield URL, or return as-is if already an ID."""
    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()


def _extract_archidekt_id(url_or_id):
    """Pull the numeric deck ID out of an Archidekt URL, or return as-is if already an ID."""
    match = re.search(r"archidekt\.com/decks/(\d+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()


def import_from_moxfield(url_or_id):
    """
    Fetch a public Moxfield decklist.
    Returns (commander_names: list[str], deck_list: list[str] with quantities expanded)
    """
    deck_id = _extract_moxfield_id(url_or_id)
    endpoint = f"https://api2.moxfield.com/v2/decks/all/{deck_id}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CommanderSimBot/1.0)"}

    try:
        resp = requests.get(endpoint, headers=headers, timeout=10)
    except requests.RequestException as e:
        raise DeckImportError(f"Network error reaching Moxfield: {e}")

    if resp.status_code != 200:
        hint = ""
        if resp.status_code in (401, 403):
            hint = "Moxfield often blocks scripted requests (this is not necessarily your deck's fault). "
        raise DeckImportError(
            f"Moxfield returned status {resp.status_code}. {hint}"
            "The deck may be private, the ID may be wrong, or Moxfield's API changed. "
            "Export the deck as a text list and import that instead."
        )

    try:
        data = resp.json()
    except ValueError:
        raise DeckImportError("Moxfield sent a response that is not valid JSON.")
    commanders = []
    if "commanders" in data:
        commanders = [c for c in data["commanders"].keys()] if isinstance(data["commanders"], dict) else []

    deck_list = []
    mainboard = data.get("mainboard", {})
    for card_name, entry in mainboard.items():
        qty = entry.get("quantity", 1)
        deck_list.extend([card_name] * qty)

    if not deck_list:
        raise DeckImportError("No mainboard cards found — deck may be empty or the response format changed.")

    return commanders, deck_list


def import_from_archidekt(url_or_id):
    """
    Fetch a public Archidekt decklist.
    Returns (commander_names: list[str], deck_list: list[str] with quantities expanded)
    """
    deck_id = _extract_archidekt_id(url_or_id)
    endpoint = f"https://archidekt.com/api/decks/{deck_id}/"

    try:
        resp = requests.get(endpoint, headers=APP_HEADERS, timeout=10)
    except requests.RequestException as e:
        raise DeckImportError(f"Network error reaching Archidekt: {e}")

    if resp.status_code != 200:
        raise DeckImportError(
            f"Archidekt returned status {resp.status_code}. "
            "The deck may be private, the ID may be wrong, or the endpoint changed. "
            "Try pasting the exported text list instead."
        )

    try:
        data = resp.json()
    except ValueError:
        raise DeckImportError("Archidekt sent a response that is not valid JSON.")
    commanders = []
    deck_list = []

    # Cards in the Maybeboard / Sideboard (or any category Archidekt marks as
    # "not included in deck") are not part of the 99 and must be skipped.
    excluded = {"Maybeboard", "Sideboard"}
    for cat in data.get("categories", []):
        if cat.get("includedInDeck") is False and cat.get("name"):
            excluded.add(cat["name"])

    for card_entry in data.get("cards", []):
        card_info = card_entry.get("card", {}).get("oracleCard", {})
        name = card_info.get("name")
        qty = card_entry.get("quantity", 1)
        categories = card_entry.get("categories") or []

        if not name:
            continue
        if any(c in excluded for c in categories):
            continue

        if "Commander" in categories:
            commanders.append(name)
        else:
            deck_list.extend([name] * qty)

    if not deck_list:
        raise DeckImportError("No cards found — deck may be empty or the response format changed.")

    return commanders, deck_list


_SECTION_HEADER = re.compile(
    r"^(commanders?|deck|mainboard|maybeboard|sideboard|companion|about)\s*(?:\(\d+\))?\s*:?\s*$",
    re.IGNORECASE,
)

# Trailing decoration that deck sites add after the card name, stripped
# repeatedly until none is left:
#   "[Ramp]" / "[Commander{top}]"   category tags
#   "*F*"                           foil marker
#   "(C21) 263"                     set code + collector number
#   "(Extended Art)"                any other trailing parenthesised note
_TRAILING_DECORATION = [
    re.compile(r"\s*\[[^\]]*\]\s*$"),
    re.compile(r"\s+\*[A-Za-z]+\*\s*$"),
    re.compile(r"\s+\([A-Za-z0-9]{2,6}\)(?:\s+\S+)?\s*$"),
    re.compile(r"\s*\([^)]*\)\s*$"),
]


_PRINTING = re.compile(r"\s+\(([A-Za-z0-9]{2,6})\)\s+(\S+)\s*$")


def printing_of(name):
    """Round ALT1: the (set code lower, collector number) a deck line names - "Sol Ring (C21) 263", "Ponder [Draw] (M12) 73 *F*",
    "Esika, God of the Tree / The Prismatic Bridge (KHM) 168" -, or None. Uses the same trailing-decoration patterns as
    _strip_printing_info, peeling them in the same order."""
    changed, found = True, None
    while changed:
        changed = False
        m = _PRINTING.search(name)
        if m and found is None:
            found = (m.group(1).lower(), m.group(2))
        for pattern in _TRAILING_DECORATION:
            new_name = pattern.sub("", name)
            if new_name != name:
                name = new_name
                changed = True
                break
    return found


def _strip_printing_info(name):
    changed = True
    while changed:
        changed = False
        for pattern in _TRAILING_DECORATION:
            new_name = pattern.sub("", name)
            if new_name != name:
                name = new_name
                changed = True
    return name.strip()


def import_from_text_with_printings(text_block):
    """Round ALT1: (commanders, deck, printings) - printings is {card name lower: (set code lower, collector number)} for the
    lines that named one. The first printing of a name wins (two printings of one name - mixed-art basics - aren't supported)."""
    printings = {}
    commanders, deck = import_from_text(text_block, printings=printings)
    return commanders, deck, printings


def import_from_text(text_block, printings=None):
    """
    Parse a plain pasted decklist, one card per line, formats like:
    '1 Sol Ring', '1x Sol Ring', or just 'Sol Ring'.
    A line that is only 'Commander' (or 'Commander (1)') starts the commander section; 'Maybeboard', 'Sideboard' and
    'Companion' sections are skipped. Trailing set/collector/foil/tag info is stripped.
    Round FMT1: a line starting with '#' is a comment (a saved deck's "# format: brawl" line, formats.py), and MTG Arena's
    'About' section ("About" / "Name My deck", at the top of an Arena export) is skipped like a sideboard.
    Returns (commander_names: list[str], deck_list: list[str])
    """
    commanders = []
    deck_list = []
    section = "deck"   # "deck", "commander", or "excluded" (maybeboard / sideboard / companion)
    buf = []           # (qty, name) lines seen so far in the CURRENT section, routed once the section ends
    first_line_qty = [0]  # quantity on the first card line that went into deck_list (list so the closure can set it)

    def flush_section():
        nonlocal buf
        if section == "commander":
            # An explicit "Commander" header normally introduces one card (two for a partner pair, or a commander
            # plus a background). Reported on Karl's PC (round 15): a "Commander" header with no blank line or
            # second header before the rest of the deck made every remaining card read as another commander (98
            # cards, 0 in the deck). So a section that grew far past 2 is read as "the header only meant its first
            # line" - the rest is the mainboard someone forgot to separate, not more commanders.
            if len(buf) <= 2:
                commanders.extend(name for _qty, name in buf)
            else:
                commanders.append(buf[0][1])
                _route_to_deck(buf[1:])
        elif section == "deck":
            _route_to_deck(buf)
        # "excluded" (maybeboard / sideboard / companion): just dropped
        buf = []

    def _route_to_deck(entries):
        for qty, name in entries:
            if not deck_list:
                first_line_qty[0] = qty
            deck_list.extend([name] * qty)

    for raw_line in text_block.splitlines():
        line = raw_line.strip()
        if not line:
            # A blank line is how every deck site separates sections, so it also ends one here - a header is
            # needed to re-enter "commander"/"excluded".
            flush_section()
            section = "deck"
            continue
        if line.startswith("#"):                 # round FMT1: a comment ("# format: brawl"); no card name starts with "#"
            continue

        # Section headers must be the WHOLE line (optionally with a count or
        # colon), e.g. "Commander", "COMMANDER (1)", "Sideboard:". Matching on
        # the first word alone would misread a card like "Commander's Sphere".
        header = _SECTION_HEADER.match(line)
        if header:
            flush_section()
            word = header.group(1).lower()
            if word in ("commander", "commanders"):
                section = "commander"
            elif word in ("deck", "mainboard"):
                section = "deck"
            else:  # maybeboard, sideboard, companion, about (MTG Arena's "About / Name ..." lines; round FMT1)
                section = "excluded"
            continue

        match = re.match(r"^(\d+)x?\s+(.+)$", line)
        if match:
            qty, name = int(match.group(1)), match.group(2).strip()
        else:
            qty, name = 1, line

        # Archidekt-style text exports can tag the commander like "[Commander{top}]".
        # (Format assumed, not yet verified against a real export.)
        tagged_commander = bool(re.search(r"\[[^\]]*\bCommander\b[^\]]*\]\s*$", name))

        if printings is not None:
            p = printing_of(name)
            stripped = _strip_printing_info(name).lower()
            if p and stripped not in printings:
                printings[stripped] = p
        name = _strip_printing_info(name)

        if tagged_commander and section != "excluded":
            commanders.append(name)          # an explicit per-line tag always wins, whatever section it is in
            continue
        buf.append((qty, name))

    flush_section()
    first_line_qty = first_line_qty[0]

    if not deck_list and not commanders:
        raise DeckImportError("Could not parse any cards from the pasted text.")

    # A commander given with no header at all, set apart only by a blank line at the very end of the paste
    # (Karl: "Pasting in a decklist should detect the commander if its at the bottom like this" - 99 unlabeled
    # cards, a blank line, then one more card by itself). Checked before the weaker "first line" guess below,
    # since an explicit blank-line gap is a much stronger signal than just being first in a 100-card list - and a
    # deck using this bottom format is still exactly 100 cards, so the first-line guess would otherwise misfire
    # and grab the actual first card in the list instead. Only fires for a single card set off by a real
    # blank-line gap, so an ordinary last line of a normal list is never mistaken for this.
    if not commanders and deck_list:
        blocks, current = [], []
        for raw_line in text_block.splitlines():
            line = raw_line.strip()
            if line.startswith("#"):             # round FMT1: comments don't make a block
                continue
            if line:
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        if len(blocks) > 1 and len(blocks[-1]) == 1:
            m = re.match(r"^(\d+)x?\s+(.+)$", blocks[-1][0])
            qty, name = (int(m.group(1)), m.group(2).strip()) if m else (1, blocks[-1][0])
            name = _strip_printing_info(name)
            if qty == 1 and name in deck_list:
                deck_list.remove(name)
                commanders = [name]

    # Moxfield's text export has NO section headers: the commander is simply the
    # first line, followed by the 99 (checked against one real export). So when
    # nothing marked a commander and the list is exactly 100 cards, treat a
    # first line with quantity 1 as the commander. This is a heuristic, not a
    # guarantee; partner decks or other layouts are not handled.
    if not commanders and len(deck_list) == 100 and first_line_qty == 1:
        commanders = [deck_list[0]]
        deck_list = deck_list[1:]

    return commanders, deck_list


def import_deck(source):
    """
    Smart dispatcher: detects Moxfield vs Archidekt URLs, otherwise treats input as pasted text.
    """
    stripped = source.strip()
    if "moxfield.com" in stripped:
        return import_from_moxfield(stripped)
    elif "archidekt.com" in stripped:
        return import_from_archidekt(stripped)
    elif "\n" in stripped or re.match(r"^\d+x?\s+\S", stripped):
        return import_from_text(stripped)
    else:
        # Ambiguous single line with no clear format - try as Moxfield ID, then Archidekt ID, then text
        raise DeckImportError(
            "Could not determine deck source. Paste a Moxfield URL, an Archidekt URL, "
            "or a full decklist as text."
        )


# ---------------------------------------------------------------------------
# flavor-name resolution
# ---------------------------------------------------------------------------
# Some Secret Lair / Universes Beyond printings put a joke or crossover-themed name on the card instead
# of its real rules name (e.g. the "Chaos Warp" in the Lord of the Rings Secret Lair drop is printed as
# "Chaos Theory"). Moxfield/Archidekt export whatever name is printed on the specific version of the card
# you picked, so a deck built with one of these in it pastes in under a name Forge's card database has
# never heard of - not a typo, not a missing card, just the wrong name for what it actually is. Confirmed
# on Karl's PC (round 15): "Chaos Theory", "Storm's Will" and "The Dead Marshes" are the flavor names for
# Chaos Warp, Jeska's Will and Urborg, Tomb of Yawgmoth respectively (checked against the Scryfall API).
_FLAVOR_CACHE_FILE = os.path.join(paths.cache_dir(), "flavor_names.json")     # round 28: cache/ moves with paths.cache_dir()


def _load_flavor_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_flavor_cache(path, cache):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError:
        pass    # the cache is a speed-up, never load-bearing


def _lookup_flavor_name(name, timeout):
    """Ask Scryfall whether `name` is the flavor name printed on some card. Returns the card's real rules name if
    so, "" if Scryfall was reached and confirmed no card goes by that name (as a real OR a flavor name - a genuine
    typo), or None if the lookup itself could not be completed (offline, timeout, Scryfall error) - a None must
    never be cached, so it can be tried again later."""
    url = "https://api.scryfall.com/cards/named?" + urlencode({"exact": name})
    try:
        resp = requests.get(url, headers=APP_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code == 404:
        return ""
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    real = data.get("name")
    flavor = data.get("flavor_name")
    if not real:
        return None
    if flavor and flavor.strip().lower() == name.strip().lower() and real.strip().lower() != name.strip().lower():
        return real
    return ""    # Scryfall knows this exact name already (as a real card Forge just doesn't have, most likely) - nothing to translate


def resolve_flavor_names(names, runtime=None, timeout=6, budget=12, cache_path=None):
    """{original_name: real_name} for every name in `names` that Forge's database does not recognise (see
    forge_client.forge_knows) but Scryfall confirms is the flavor name printed on a specific alternate-art version
    of a different, real card that Forge DOES recognise. Never raises: offline or a Scryfall hiccup just means
    nothing gets resolved this time (the name stays flagged as unknown, same as before this existed). Looks up at
    most `budget` never-before-seen names per call so a paste full of typos cannot hang the screen; anything already
    looked up (a hit or a confirmed miss) is cached to disk and never looked up again."""
    import forge_client as fc
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or fc.DEFAULT_RUNTIME
    seen = set()
    unknown = []
    for n in names:
        n = (n or "").strip()
        if n and n not in seen and fc.forge_knows(n, runtime) is False:
            seen.add(n)
            unknown.append(n)
    if not unknown:
        return {}

    cache_path = cache_path or _FLAVOR_CACHE_FILE
    cache = _load_flavor_cache(cache_path)
    resolved = {}
    dirty = False
    for n in unknown:
        key = n.lower()
        if key in cache:
            if cache[key] and fc.forge_knows(cache[key], runtime):
                resolved[n] = cache[key]
            continue
        if budget <= 0:
            continue    # too many unseen names this call; the rest stay unknown until next time (or a smaller paste)
        budget -= 1
        real = _lookup_flavor_name(n, timeout)
        if real is None:
            continue    # lookup failed - do not cache, try again later
        cache[key] = real
        dirty = True
        if real and fc.forge_knows(real, runtime):
            resolved[n] = real
    if dirty:
        _save_flavor_cache(cache_path, cache)
    return resolved
