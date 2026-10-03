# SPDX-License-Identifier: GPL-3.0-or-later
"""
legality.py - is a deck legal in a format? (Round BAN1: Commander; Round FMT1: MTG Arena's 100-card Brawl too, formats.py).
No pygame, no I/O on the caller's thread.

Karl, 1 Oct 2026: "If a card being imported isn't format legal, flag it to the player and give the deck a not legal label but let them
play it anyway" - and NOT LEGAL covers Commander's deck-building rules too (100 cards, singleton, colour identity, commander
eligibility, commander pairs). Nothing here ever stops a game: every line it gives deck_library is non-blocking.

Three sources, because each goes stale differently:
  the banned list   one Scryfall search (banned:<fmt>), cached in cache/banned_<fmt>.json, refreshed in the background when older than
                    a week; the bundled banned_<fmt>.snapshot.json when there is no cache (tools/build_banlist.py writes it)
  card data         card_data.CardDataStore's cached Scryfall objects (legalities, color_identity, type_line, oracle_text, keywords,
                    card_faces); fetched in the background by ensure_card_data(), read with peek_card() (never the network)
  card counts       the deck list itself

The rules (Comprehensive Rules of 25 Sep 2026, MagicCompRules 20260925.txt):
  903.3   a commander is a legendary creature card, Vehicle card, or Spacecraft card with a power/toughness box;
  903.3a  or a card whose text says it can be your commander
  903.5a  exactly 100 cards including the commander(s)      903.5b  singleton except basic lands (and cards that say otherwise)
  903.5c  colour identity within the commanders'             702.124 the partner abilities: partner, partner-[text] (Character
          select, Father & son, Friends forever, Survivors), partner with [name], choose a Background, Doctor's companion;
          different partner abilities can't be combined (702.124f)

Round FMT1, Brawl (MTG Arena's 100-card Brawl; the Brawl option, 903.12, with Arena's 100 cards): the same checks with
  903.12c  a legendary planeswalker can be the commander too
  903.12e  a commander with no colours in its colour identity: any number of basic lands of ONE basic land type (the type the
           deck has most of counts as the chosen one; basics of the other types are outside its colours)
  Arena's card pool and banned list: Scryfall's "brawl" legality and banned:brawl (cache/banned_brawl.json,
  banned_brawl.snapshot.json). 25/30 life and no commander damage are the engine's (Forge's Brawl variant), not a deck rule.

A check that couldn't run makes the deck UNCHECKED, never silently legal.
"""
from collections import namedtuple
import datetime
import json
import os
import re
import threading
import time

import formats
import paths

FORMATS = ("commander", "brawl")        # round FMT1: formats.FORMATS's keys, each with its own banned list
MAX_AGE_DAYS = 7
SEARCH_URL = "https://api.scryfall.com/cards/search"
HEADERS = {"User-Agent": "CommanderSim/0.1 (personal hobby project)", "Accept": "application/json;q=0.9,*/*;q=0.8"}

# Forge (3a74143) moves a [Commander] card that isn't a legendary creature (or a planeswalker) into the library without a word
# (claude/BAN1_FORGE_CHECK_2026-10-02.md). Karl, 2 Oct: "Yes only legal commanders" - so a commander that can't be one (CR 903.3)
# stops Start, the one legality line that does. A commander whose card data isn't loaded is UNCHECKED and does not block.
INELIGIBLE_COMMANDER_BLOCKS = True

BanList = namedtuple("BanList", "names as_of source")
Verdict = namedtuple("Verdict", "status issues unchecked as_of")        # status: "legal" | "not_legal" | "unchecked"

BASIC_TYPES = ("Plains", "Island", "Swamp", "Mountain", "Forest")        # round FMT1 (903.12e); Wastes has no basic land type
BASIC_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"} | \
    {"snow-covered " + b for b in ("plains", "island", "swamp", "mountain", "forest", "wastes")}
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen "
    "nineteen twenty".split())}
ANY_NUMBER = re.compile(r"a deck can have any number of cards named", re.I)
UP_TO = re.compile(r"a deck can have up to (\w+) cards named", re.I)
CAN_BE_COMMANDER = re.compile(r"can be your commander", re.I)
PARTNER_TEXT_KNOWN = ("character select", "father & son", "friends forever", "survivors")
PARTNER_LIKE = re.compile(r"partner|companion|friends|background", re.I)
KNOWN_KEYWORDS = {"partner", "partner with", "friends forever", "choose a background", "doctor's companion"}
_FACE_SPLIT = re.compile(r"\s*/{1,2}\s*")

_lock = threading.RLock()
_BANNED = {fmt: BanList(frozenset(), None, "none") for fmt in FORMATS}
_version = 0
_store = None
_fetching = set()                       # card names a background thread is fetching now
_refreshing = set()                     # formats being refreshed now


def version():
    """Changes whenever the banned list or the card data changes: part of DeckEntry.problems' cache key."""
    return _version


def _bump():
    global _version
    with _lock:
        _version += 1


def _note(text):
    try:
        import crashlog
        crashlog.note(text)
    except Exception:
        pass


# ---- the banned list ------------------------------------------------------------------------------------------------------------
def cache_file(fmt="commander"):
    return os.path.join(paths.cache_dir(), f"banned_{fmt}.json")


def snapshot_file(fmt="commander"):
    return os.path.join(paths.program_dir(), f"banned_{fmt}.snapshot.json")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cards = data.get("cards")
    if not isinstance(cards, list) or not all(isinstance(c, str) for c in cards):
        raise ValueError("no card list")
    return data


def _names(cards):
    """Every name a banned card can be matched by: its full name and each face, lower case."""
    out = set()
    for c in cards:
        out.add(c.strip().lower())
        out |= {p.strip().lower() for p in _FACE_SPLIT.split(c) if p.strip()}
    return frozenset(out)


def load(fmt="commander"):
    """The cache, else the bundled snapshot, else nothing. Cheap; local files only."""
    for path, source in ((cache_file(fmt), "scryfall"), (snapshot_file(fmt), "snapshot")):
        try:
            data = _read(path)
        except (OSError, ValueError):
            continue
        with _lock:
            _BANNED[fmt] = BanList(_names(data["cards"]), data.get("fetched_at"), source)
        _bump()
        return _BANNED[fmt]
    with _lock:
        _BANNED[fmt] = BanList(frozenset(), None, "none")
    return _BANNED[fmt]


def banned(fmt="commander"):
    return _BANNED[fmt]


def fetch_banned(fmt="commander", session=None, pause=0.1):
    """The live list from Scryfall, following its pages. Raises on any failure."""
    import requests
    s = session or requests.Session()
    url, params, names = SEARCH_URL, {"q": f"banned:{fmt}", "unique": "cards"}, []
    while url:
        resp = s.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"Scryfall answered HTTP {resp.status_code}")
        page = resp.json()
        names += [c["name"] for c in page.get("data", [])]
        url, params = (page.get("next_page"), None) if page.get("has_more") else (None, None)
        if url:
            time.sleep(pause)
    if not names:
        raise RuntimeError("Scryfall sent an empty banned list")
    return sorted(set(names))


def write_list(path, names, fmt="commander"):
    data = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "source": "scryfall",
            "query": f"banned:{fmt}", "cards": names}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return data


def needs_refresh(fmt="commander", max_age_days=MAX_AGE_DAYS, now=None):
    try:
        age = (now or time.time()) - os.path.getmtime(cache_file(fmt))
    except OSError:
        return True
    return age > max_age_days * 86400


def refresh(fmt="commander", session=None):
    """Fetch, write the cache atomically, swap the list in. On failure keep the old list and note it once. Returns True on success."""
    try:
        names = fetch_banned(fmt, session)
        data = write_list(cache_file(fmt), names, fmt)
    except Exception as e:
        _note(f"Banned list ({fmt}) not refreshed: {type(e).__name__}: {e}")
        return False
    with _lock:
        _BANNED[fmt] = BanList(_names(names), data["fetched_at"], "scryfall")
    _bump()
    return True


def refresh_in_background(fmt="commander", max_age_days=MAX_AGE_DAYS):
    """Starts one daemon thread when the cached list is missing or stale. Returns the thread, or None."""
    if os.environ.get("MANTICORE_OFFLINE") or not needs_refresh(fmt, max_age_days):
        return None
    with _lock:
        if fmt in _refreshing:
            return None
        _refreshing.add(fmt)

    def run():
        try:
            refresh(fmt)
        finally:
            with _lock:
                _refreshing.discard(fmt)
    t = threading.Thread(target=run, name=f"banlist-{fmt}", daemon=True)
    t.start()
    return t


# ---- card data --------------------------------------------------------------------------------------------------------------------
def set_store(store):
    """The CardDataStore the checks read (the table's own). A store without peek_card (a test stand-in) counts as none."""
    global _store
    _store = store if hasattr(store, "peek_card") and hasattr(store, "prefetch_cards") else None


def get_store():
    return _store


def ensure_card_data(names, store=None):
    """Fetch, in one background thread, the cards the store doesn't have yet; bumps version() when done. Returns the thread or None."""
    store = store or _store
    if store is None or not hasattr(store, "peek_card") or os.environ.get("MANTICORE_OFFLINE"):
        return None
    with _lock:
        need = sorted({n for n in names if n and store.peek_card(n) is None and n not in _fetching})
        if not need:
            return None
        _fetching.update(need)

    def run():
        try:
            store.prefetch_cards(need)
        except Exception as e:
            _note(f"Card data for the legality check not fetched: {type(e).__name__}: {e}")
        finally:
            with _lock:
                _fetching.difference_update(need)
            _bump()
    t = threading.Thread(target=run, name="legality-cards", daemon=True)
    t.start()
    return t


def start(commanders_and_decks=(), store=None, fmt=None):
    """What the deck screen calls when it opens or a paste is saved: the list from disk, a refresh if stale, card data.
    fmt None (round FMT1): every format's banned list."""
    if store is not None:
        set_store(store)
    for f in (FORMATS if fmt is None else (fmt,)):
        load(f)
        refresh_in_background(f)
    names = set()
    for cmd, deck in commanders_and_decks:
        names |= set(cmd or ()) | set(deck or ())
    if names:
        ensure_card_data(names)


# ---- reading a card ---------------------------------------------------------------------------------------------------------------
def _front(card):
    faces = card.get("card_faces") or []
    return faces[0] if faces and card.get("layout") not in ("split", "flip", "adventure") else card


def _type_line(card):
    return (_front(card).get("type_line") or card.get("type_line") or "")


def _oracle(card):
    faces = card.get("card_faces") or []
    texts = [card.get("oracle_text") or ""] + [f.get("oracle_text") or "" for f in faces]
    return "\n".join(t for t in texts if t)


def _is_basic(name, card):
    if card is not None:
        return "basic" in _type_line(card).lower().split()
    return name.strip().lower() in BASIC_NAMES


def _copy_limit(card):
    """None: one copy (903.5b); math.inf-like large: any number; N: up to N."""
    text = _oracle(card) if card else ""
    if ANY_NUMBER.search(text):
        return 10 ** 6
    m = UP_TO.search(text)
    if m:
        word = m.group(1).lower()
        return NUMBER_WORDS.get(word) or (int(word) if word.isdigit() else None)
    return None


def can_be_commander(card, fmt="commander"):
    """CR 903.3 / 903.3a (and 903.12c for Brawl: a legendary planeswalker too). (True, "") or (False, why)."""
    if CAN_BE_COMMANDER.search(_oracle(card)):
        return True, ""
    front = _front(card)
    tl = (front.get("type_line") or "").lower()
    words = re.split(r"[\s—-]+", tl)
    if "legendary" not in words:
        return False, "not legendary"
    if "creature" in words or "vehicle" in words:
        return True, ""
    if "planeswalker" in words and formats.get(fmt).planeswalker_commanders:
        return True, ""
    if "spacecraft" in words and (front.get("power") is not None or card.get("power") is not None):
        return True, ""
    if "background" in words:
        return False, "a Background needs a commander with Choose a Background"
    return False, ("not a creature or planeswalker" if formats.get(fmt).planeswalker_commanders else "not a creature")


def partner_abilities(card):
    """The partner abilities a card has (CR 702.124a), as tuples, plus any partner-like words this code doesn't know."""
    found, unknown = set(), set()
    for line in _oracle(card).split("\n"):
        ln = line.strip()
        low = re.sub(r"\s*\(.*?\)\s*", "", ln).strip().lower()           # drop reminder text
        if not low:
            continue
        if low == "partner":
            found.add(("partner",))
        elif low.startswith("partner with "):
            found.add(("with", low[len("partner with "):].strip()))
        elif low.startswith("partner—") or low.startswith("partner-") or low.startswith("partner —"):
            what = re.split(r"—|-", low, 1)[1].strip()
            (found if what in PARTNER_TEXT_KNOWN else unknown).add(("text", what))
        elif low == "friends forever":                                     # printed before it became partner-Friends forever
            found.add(("text", "friends forever"))
        elif low == "choose a background":
            found.add(("background",))
        elif low.startswith("doctor’s companion") or low.startswith("doctor's companion"):
            found.add(("companion",))
    for kw in card.get("keywords") or []:
        k = kw.lower().replace("\u2019", "'")
        if not PARTNER_LIKE.search(k) or k in KNOWN_KEYWORDS:
            continue
        if k.startswith("partner") and re.split(r"\u2014|-", k, 1)[-1].strip() in PARTNER_TEXT_KNOWN:
            continue
        if not any(a[0] == "text" for a in found | unknown):
            unknown.add(("keyword", kw))
    return found, unknown


def _is_background(card):
    tl = _type_line(card).lower()
    return "legendary" in tl and "background" in tl and "enchantment" in tl


def _is_time_lord_doctor(card):
    tl = _type_line(card)
    if "Legendary" not in tl or "Creature" not in tl or "—" not in tl:
        return False
    return tl.split("—", 1)[1].split() == ["Time", "Lord", "Doctor"]


def pair_ok(a, b, name_a, name_b):
    """(ok, unknown_keyword_or_None, why) for two commanders (CR 702.124)."""
    pa, ua = partner_abilities(a)
    pb, ub = partner_abilities(b)
    if ("partner",) in pa and ("partner",) in pb:
        return True, None, ""
    if ("with", name_b.lower()) in pa and ("with", name_a.lower()) in pb:
        return True, None, ""
    if any(x[0] == "text" and x in pb for x in pa):
        return True, None, ""
    if (("background",) in pa and _is_background(b)) or (("background",) in pb and _is_background(a)):
        return True, None, ""
    if (("companion",) in pa and _is_time_lord_doctor(b)) or (("companion",) in pb and _is_time_lord_doctor(a)):
        return True, None, ""
    unknown = sorted(ua | ub)
    if unknown:
        kind, what = unknown[0]
        return False, ("Partner—" + what.title() if kind == "text" else what), ""
    if not pa and not _is_background(a):
        return False, None, f"{name_a} has no partner ability"
    if not pb and not _is_background(b):
        return False, None, f"{name_b} has no partner ability"
    return False, None, "their partner abilities don't match"


# ---- the verdict ------------------------------------------------------------------------------------------------------------------
def check(commanders, deck, store=None, fmt="commander"):
    """Verdict(status, issues [(kind, text, cards)], unchecked [(what, detail)], as_of). Never touches the network."""
    store = store or _store
    commanders, deck = list(commanders or []), list(deck or [])
    everything = commanders + deck
    peek = store.peek_card if hasattr(store, "peek_card") else (lambda n: None)
    data = {n: peek(n) for n in set(everything)}
    missing = sorted(n for n, c in data.items() if c is None)
    issues, unchecked = [], []
    bl = _BANNED[fmt]

    # card rules
    if bl.source == "none":
        unchecked.append(("banned list", None))
    else:
        hit = [n for n in dict.fromkeys(everything)
               if n.strip().lower() in bl.names or any(p.strip().lower() in bl.names for p in _FACE_SPLIT.split(n) if p.strip())
               or (data[n] is not None and data[n].get("name", "").lower() in bl.names)]
        if hit:
            issues.append(("banned", "", hit))
    else_banned = {i for k, _t, cards in issues if k == "banned" for i in cards}
    nl = [n for n in dict.fromkeys(everything) if n not in else_banned and data[n] is not None
          and (data[n].get("legalities") or {}).get(fmt) == "not_legal"]
    if nl:
        issues.append(("not_legal", "", nl))

    # 903.5a size (Arena's Brawl: 100 too)
    total = len(everything)
    size = formats.get(fmt).deck_size
    if total != size:
        issues.append(("size", f"This deck has {total} cards; a {formats.name(fmt)} deck has {size} (commander included).", []))

    # 903.5b singleton
    counts = {}
    for n in everything:
        counts[n] = counts.get(n, 0) + 1
    extra = []
    for n, k in counts.items():
        if k < 2 or _is_basic(n, data[n]):
            continue
        limit = _copy_limit(data[n])
        if limit is None or k > limit:
            extra.append(f"{n} ×{k}")
    if extra:
        issues.append(("singleton", "", extra))

    # 903.3 commander eligibility, 702.124 pairs
    cmd_cards = [(n, data.get(n)) for n in commanders]
    if commanders and any(c is None for _n, c in cmd_cards):
        unchecked.append(("commander", None))
    elif commanders:
        bad = []
        background_ok = len(cmd_cards) == 2 and any(("background",) in partner_abilities(c)[0] for _n, c in cmd_cards)
        for n, c in cmd_cards:
            ok, why = can_be_commander(c, fmt)
            if not ok and not (background_ok and _is_background(c)):
                bad.append(f"{n} ({why})")
        if bad:
            issues.append(("commander", "", bad))
        if len(commanders) > 2:
            issues.append(("pair", "Only one or two commanders are allowed.", commanders))
        elif len(commanders) == 2:
            (na, ca), (nb, cb) = cmd_cards
            ok, unknown, why = pair_ok(ca, cb, na, nb)
            if unknown:
                unchecked.append(("commander pair", unknown))
            elif not ok:
                issues.append(("pair", f"These commanders can't be paired: {na} + {nb} ({why}).", commanders))

    # 903.5c colour identity
    if commanders and any(c is None for _n, c in cmd_cards):
        unchecked.append(("colour identity", None))
    elif commanders:
        allowed = set()
        for _n, c in cmd_cards:
            allowed |= set(c.get("color_identity") or [])
        out = [n for n in dict.fromkeys(deck) if data[n] is not None and not set(data[n].get("color_identity") or []) <= allowed]
        if out and not allowed and fmt == "brawl":
            out = _colourless_basics_ok(out, deck, data)          # 903.12e
        if out:
            who = commanders[0].split(",")[0] if len(commanders) == 1 else " + ".join(c.split(",")[0] for c in commanders)
            colours = "/".join(c for c in "WUBRG" if c in allowed) or "colourless"
            issues.append(("identity", f"Outside {who}'s colours ({colours})", out))

    if missing:
        unchecked.append(("card data", len(missing)))
    status = "not_legal" if issues else ("unchecked" if unchecked else "legal")
    return Verdict(status, issues, unchecked, bl.as_of)


def _basic_types(card):
    """The basic land types of a basic land card ("Basic Snow Land — Island" -> ["Island"]); [] for anything else."""
    tl = _type_line(card or {})
    if "Basic" not in tl.split() or "—" not in tl:
        return []
    sub = tl.split("—", 1)[1].split()
    return [t for t in BASIC_TYPES if t in sub]


def _colourless_basics_ok(out, deck, data):
    """Round FMT1, CR 903.12e: a Brawl deck whose commander has no colours may have any number of basic lands of one basic land
    type of its choice. The deck doesn't say which, so the type it has most of (ties: W, U, B, R, G order) is taken as chosen.
    Returns `out` without those basics."""
    per_type = {}
    for n in deck:
        for t in _basic_types(data.get(n)):
            per_type[t] = per_type.get(t, 0) + 1
    if not per_type:
        return out
    chosen = max(BASIC_TYPES, key=lambda t: (per_type.get(t, 0), -BASIC_TYPES.index(t)))
    return [n for n in out if chosen not in _basic_types(data.get(n))]


def _shown(cards, n=6):
    return ", ".join(cards[:n]) + (f" and {len(cards) - n} more" if len(cards) > n else "")


def _date(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b %Y").lstrip("0")
    except (AttributeError, ValueError):
        return "unknown date"


def problem_lines(verdict, fmt="commander"):
    """The lines deck_library.describe_problems shows: [(text, is_blocking)], every one non-blocking (but see
    INELIGIBLE_COMMANDER_BLOCKS)."""
    name = formats.name(fmt)
    out = []
    for kind, text, cards in verdict.issues:
        if kind == "banned":
            out.append((f"Banned in {name} (list as of {_date(verdict.as_of)}): {_shown(cards)}.", False))
        elif kind == "not_legal":
            why = " (not on MTG Arena)" if formats.normal(fmt) == "brawl" else " at all"      # round FMT1: Arena's card pool
            out.append((f"Not legal in {name}{why}: {_shown(cards)}.", False))
        elif kind == "size":
            out.append((text, False))
        elif kind == "singleton":
            out.append((f"More than one copy: {_shown(cards)}.", False))
        elif kind == "identity":
            out.append((f"{text}: {_shown(cards)}.", False))
        elif kind == "commander":
            if INELIGIBLE_COMMANDER_BLOCKS:
                what = ("a legendary creature or planeswalker" if formats.get(fmt).planeswalker_commanders
                        else "a legendary creature")
                who = "A commander" if formats.normal(fmt) == formats.DEFAULT else f"A {name} commander"
                out.append((f"Can't be a commander: {_shown(cards)}. {who} must be {what} (or say it can be "
                            "your commander) - change it before starting.", True))
            else:
                out.append((f"Can't be a commander: {_shown(cards)}. Forge will shuffle it into your library, and you'll play "
                            "with no commander." if len(cards) == 1 else
                            f"Can't be a commander: {_shown(cards)}. Forge will shuffle them into your library.", False))
        elif kind == "pair":
            out.append((text, False))
    for what, detail in verdict.unchecked:
        if what == "card data":
            out.append((f"Legality not checked yet for {detail} card{'s' if detail != 1 else ''} (card data still loading, or no "
                        "internet).", False))
        elif what == "banned list":
            out.append(("Banned list not checked (no internet and no saved list).", False))
        elif what == "commander pair":
            out.append((f"Commander pair not checked: {detail} isn't known to this version.", False))
    if verdict.status == "not_legal":
        if any(blocking for _t, blocking in out):
            out.append(("Only the commander stops this deck from starting; the other lines are warnings.", False))
        else:
            out.append(("You can still play this deck. It's just marked Not legal.", False))
    return out


def blocks_start(verdict):
    """True when the deck can't start: an ineligible commander, with INELIGIBLE_COMMANDER_BLOCKS on (Karl, 2 Oct)."""
    return bool(verdict) and INELIGIBLE_COMMANDER_BLOCKS and any(k == "commander" for k, _t, _c in verdict.issues)


UNCHECKED_PREFIXES = ("Legality not checked yet", "Banned list not checked", "Commander pair not checked")


def is_unchecked_line(text):
    """True for a line that only says a check couldn't run (tests use it: a test run has no card data or banned list)."""
    return text.startswith(UNCHECKED_PREFIXES)


def label(verdict):
    """The deck screen's tag: "NOT LEGAL", "UNCHECKED" or None."""
    return {"not_legal": "NOT LEGAL", "unchecked": "UNCHECKED"}.get(verdict.status) if verdict else None
