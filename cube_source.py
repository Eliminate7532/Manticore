# SPDX-License-Identifier: GPL-3.0-or-later
"""
cube_source.py - a cube from Cube Cobra, for the Commander cube draft (Round CD1). No pygame.

Karl, 9 Oct 2026 (docs/incoming/cd1/SPEC_CUBE_DRAFT_2026-10-09.md, answer 4): any Cube Cobra cube by its link, and Manticore ships one default -
Commander Cube by tritoch (cubecobra.com/cube/overview/cm, answer 11).

Where the list comes from: Cube Cobra's public cube download, https://cubecobra.com/cube/api/cubeJSON/<id>. Its source file says
"// CORS middleware for public API" and allows 100 requests a minute (packages/server/src/router/routes/cube/api/cubeJSON.ts at
2de88dd, read 9 Oct). It carries every card's Scryfall data (type, colour identity, mana cost) and Cube Cobra's Elo - a rating
updated from real drafts on Cube Cobra: the picked card wins an Elo match against every card left in the pack (update_draft_history.ts,
lines 413-440). The Elo in this download is the rating over all cubes, not this cube's own (update_cards.ts line 792 sets it from
update_metadata_dict.ts line 488, eloByOracleId). Forge's own cube rankings (res/draft/rankings_cubecobra.txt, 4,000 names) covered
46% and 49% of two Commander cubes on 9 Oct; the Elo covered 100% of both. That is why the draft runs here, not in Forge's BoosterDraft.

The download is kept in the cache (paths.cache_dir()/cubes/<id>.json) with the time it was fetched, and used from there until a refresh
is asked for; with no network the kept copy is used. MANTICORE_OFFLINE=1 (the test suite) never fetches.

Only the cube's mainboard is drafted. A card Forge has no script for can't be played, so check_forge() leaves it out of the packs and
names it; a card Forge's developers marked "AI:RemoveDeck:All" (the AI misplays it) stays draftable - people draft it - and is named
so the AI's deck builder can leave it out when it has a substitute (draft_deck.py).

Round CD3 - the stock cubes (Karl, 12 Oct 2026: "a small selection of 'stock' cubes that come built in"; then: classic draft for the
classic cubes, the lists bundled in the program with their owners credited, Turbo Cube with its emblem, Commander Cube bundled too).
STOCK_CUBES lists them in the draft window's order. Each one's list is in stock_cubes/<key>.json next to the program - Cube Cobra's
own cubeJSON answer, trimmed to the fields this module reads (tools/build_stock_cubes.py makes them and says when) - so a stock cube
loads with no network, and never changes under a player until a new version of the program ships a newer list. A pasted link to a
stock cube gets the bundled list too. Each stock cube has a draft style ("commander": Commander Draft; "classic": 3 packs of 15,
one pick at a time, 40-card decks, 20 life - draft_pod.STYLES) and any house rule (house_rules.py: Turbo Cube's emblem). A cube that
isn't stock has its style guessed (guess_style: Commander Draft when Cube Cobra's tags or name say Commander or EDH) and the draft
window lets the player change it. Every cube keeps Cube Cobra's "basics" board: the basic lands its owner offers (basic_station).
"""
from collections import namedtuple
import json
import os
import re
import time

CUBE_JSON_URL = "https://cubecobra.com/cube/api/cubeJSON/{id}"
CUBE_PAGE_URL = "https://cubecobra.com/cube/overview/{id}"
DEFAULT_CUBE = "cm"                      # Commander Cube by tritoch (Karl, 9 Oct, answer 11)
FETCH_TIMEOUT = 30
MAX_BYTES = 40 * 1024 * 1024             # the two Commander cubes measured 9 Oct: 1.9 MB and 2.3 MB
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_URL = re.compile(r"cubecobra\.com/cube/(?:overview|list|playtest|analysis|about|blog|records|api/cubeJSON|download/[a-z]+)/"
                  r"([A-Za-z0-9_-]{1,64})", re.I)
AI_FLAG = re.compile(r"^AI:RemoveDeck:All\s*$", re.M)


class CubeError(Exception):
    """Something a player can read: why this cube can't be loaded."""


CubeCard = namedtuple("CubeCard", "name oracle_id type_line oracle_text cost cmc colors identity elo power toughness keywords "
                                  "set number scryfall_id commander")
# Round CD3 added the last five fields (with defaults, so a Cube made the old way still works): basics - the names on Cube Cobra's
# basics board; tags - Cube Cobra's tags; style - "commander" | "classic" (a stock cube's, else guess_style's); rules - house rules
# (house_rules.py keys); stock - True when the list is the bundled one.
Cube = namedtuple("Cube", "id name owner updated cards fetched basics tags style rules stock",
                  defaults=((), (), None, (), False))
ForgeCheck = namedtuple("ForgeCheck", "playable missing ai_flagged")

STYLES = ("commander", "classic")
# Round CD3: the stock cubes, in the draft window's order. key = the id in their Cube Cobra links (cubecobra.com/cube/list/<key>);
# label = the button's words; name and owner = Cube Cobra's (the bundled file says the same - tested).
Stock = namedtuple("Stock", "key label name owner style rules")
STOCK_CUBES = (
    Stock("cm", "Commander Cube", "Commander Cube", "tritoch", "commander", ()),
    Stock("2edc8e87-440c-4e63-8c93-1d2842de4909", "Entry-level Cubing", "Entry-level Cubing", "DeepSky8", "classic", ()),
    Stock("modovintage", "MTGO Vintage", "MTGO Vintage Cube", "Dekkaru", "classic", ()),
    Stock("cthulhu", "Cube of Cthulhu", "The Cube of Cthulhu", "CavsFangelo", "classic", ()),
    Stock("turbo", "Turbo Cube", "Turbo Cube", "anthonymattox", "classic", ("turbo",)),
    Stock("e1a51d49-9a07-48b1-b2ba-c78b6e65d03b", "LotR 360", "Lord of the Rings (LotR) standalone 360", "zarocks", "classic", ()),
    Stock("devoid", "Devoid Cube", "The Devoid Cube", "Isticle", "classic", ()),
    Stock("spooty", "Spooty Peasant", "The Spooty Peasant Cube - 2026 3.0 Edition", "Spootyone", "classic", ()),
)
STOCK_DIR_NAME = "stock_cubes"


def parse_ref(text):
    """The cube id in a Cube Cobra link or a bare id ("cm", "https://cubecobra.com/cube/overview/cm", ".../list/cm?view=x").
    Raises CubeError for anything else."""
    t = (text or "").strip()
    if not t:
        raise CubeError("Paste a Cube Cobra link or a cube's id.")
    m = _URL.search(t)
    if m:
        return m.group(1)
    if "/" not in t and _ID.match(t):
        return t
    raise CubeError("That isn't a Cube Cobra cube link (it should look like cubecobra.com/cube/overview/<id>).")


def user_agent():
    try:
        import version
        v = version.VERSION
    except Exception:
        v = "?"
    return f"Manticore/{v} (+https://github.com/Eliminate7532/Manticore)"


# ---- the cards -------------------------------------------------------------------------------------------------------------------
def scryfall_like(card):
    """The fields legality.can_be_commander / pair_ok read, from a CubeCard."""
    return {"name": card.name, "type_line": card.type_line, "oracle_text": card.oracle_text, "power": card.power}


def _can_command(card):
    try:
        import legality
        ok, _why = legality.can_be_commander(scryfall_like(card), "commander")
        return bool(ok)
    except Exception:                                            # never let the rules module stop a cube from loading
        t = card.type_line.lower()
        return ("can be your commander" in card.oracle_text.lower()) or ("legendary" in t and "creature" in t)


def card_from_details(d):
    """A CubeCard from one Cube Cobra card's "details"."""
    def tup(x):
        return tuple(str(v).upper() for v in (x or ()) if str(v).strip())
    try:
        elo = float(d.get("elo"))
    except (TypeError, ValueError):
        elo = 1200.0                                             # Cube Cobra's DefaultElo for a card nobody has drafted
    try:
        cmc = float(d.get("cmc") or 0)
    except (TypeError, ValueError):
        cmc = 0.0
    card = CubeCard(name=str(d.get("name") or "").strip(), oracle_id=str(d.get("oracle_id") or ""),
                    type_line=str(d.get("type") or d.get("type_line") or ""), oracle_text=str(d.get("oracle_text") or ""),
                    cost=tuple(str(s).lower() for s in (d.get("parsed_cost") or ())), cmc=cmc, colors=tup(d.get("colors")),
                    identity=tuple(c for c in "WUBRG" if c in tup(d.get("color_identity"))), elo=elo,
                    power=d.get("power"), toughness=d.get("toughness"), keywords=tuple(d.get("keywords") or ()),
                    set=str(d.get("set") or ""), number=str(d.get("collector_number") or ""),
                    scryfall_id=str(d.get("scryfall_id") or ""), commander=False)
    return card._replace(commander=_can_command(card))


def is_land(card):
    return "land" in re.split(r"[\s—-]+", card.type_line.split("//")[0].lower())


def is_basic(card):
    return card.type_line.lower().startswith("basic") or card.name.lower() in BASIC_BY_COLOUR.values() or card.name == "Wastes"


BASIC_BY_COLOUR = {"W": "plains", "U": "island", "B": "swamp", "R": "mountain", "G": "forest"}
BASIC_NAME = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest", "": "Wastes"}


def from_cubecobra(data, cube_id=None):
    """A Cube from Cube Cobra's cubeJSON answer (only its mainboard)."""
    if not isinstance(data, dict):
        raise CubeError("Cube Cobra sent something that isn't a cube.")
    cards = data.get("cards") or {}
    main = cards.get("mainboard") if isinstance(cards, dict) else cards
    if not isinstance(main, list) or not main:
        raise CubeError("This cube has no cards in its main list.")
    out = []
    for entry in main:
        d = entry.get("details") if isinstance(entry, dict) else None
        if not isinstance(d, dict) or not d.get("name"):
            continue
        out.append(card_from_details(d))
    if not out:
        raise CubeError("This cube's cards came without their details.")
    owner = data.get("owner")
    owner = owner.get("username") if isinstance(owner, dict) else (owner if isinstance(owner, str) else "")
    basics = []                                                   # round CD3: the owner's basic lands (cards.basics)
    for entry in (cards.get("basics") or []) if isinstance(cards, dict) else []:
        d = entry.get("details") if isinstance(entry, dict) else None
        if isinstance(d, dict) and d.get("name"):
            basics.append(str(d["name"]).strip())
    tags = tuple(str(t) for t in (data.get("tags") or ()) if isinstance(t, (str, int)))
    name = str(data.get("name") or "").strip()
    prefixes = tuple(str(p) for p in (data.get("categoryPrefixes") or ()) if isinstance(p, str))
    share = sum(1 for c in out if c.commander) / len(out)
    return Cube(id=cube_id or str(data.get("shortId") or data.get("id") or ""), name=name,
                owner=owner or "", updated=data.get("dateLastUpdated"), cards=tuple(out), fetched=None,
                basics=tuple(basics), tags=tags,
                style=guess_style(name, tags, data.get("categoryOverride"), prefixes, share))


COMMANDER_SHARE = 0.15       # round CD3: a cube with no other sign is a Commander cube from this share of possible commanders


def guess_style(name, tags, category=None, prefixes=(), commander_share=0.0):
    """Round CD3: the draft style of a cube that isn't stock - only a guess: the draft window shows it and the player can change it.
      "commander"  its name, tags or Cube Cobra category prefixes say Commander or EDH (Commander Cube's tags: "commander",
                   "commander cube", "edh cube" ...)
      "classic"    otherwise, when its owner gave it a category (Vintage, Set, Custom ... - the classic cubes' way)
      by share     else "commander" when at least COMMANDER_SHARE of its cards can be commanders (Commander Cube 19%; the classic
                   stock cubes 3-11%, measured 12 Oct - the LotR set cube's 22% has a "Set" category), else "classic"
    A cube with no name, tags or category and many legends stays a Commander Draft, as every cube was before CD3."""
    words = " ".join([name or ""] + [str(t) for t in tags or ()] + [str(p) for p in prefixes or ()]).lower()
    if re.search(r"\bcommander\b|\bedh\b", words):
        return "commander"
    if category:
        return "classic"
    return "commander" if commander_share >= COMMANDER_SHARE else "classic"


# ---- round CD3: the stock cubes ------------------------------------------------------------------------------------------------
def stock_entry(cube_id):
    """The Stock entry for this cube id (case doesn't matter), or None."""
    k = (cube_id or "").strip().lower()
    return next((st for st in STOCK_CUBES if st.key.lower() == k), None)


def stock_dir():
    import paths
    return os.path.join(paths.program_dir(), STOCK_DIR_NAME)


def stock_path(key, folder=None):
    return os.path.join(folder or stock_dir(), f"{key}.json")


def read_stock(key, folder=None):
    """(data, fetched time) of a bundled stock list, or (None, None) when the file is missing or unreadable."""
    try:
        with open(stock_path(key, folder), encoding="utf-8") as f:
            kept = json.load(f)
        if isinstance(kept, dict) and isinstance(kept.get("cube"), dict):
            return kept["cube"], kept.get("fetched")
    except (OSError, ValueError):
        pass
    return None, None


BASIC_BY_NAME = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G", "wastes": ""}


def basic_station(cube):
    """Round CD3: {colour: basic land name} a deck from this cube adds freely ("" = colourless: Wastes). The cube's own basics
    board decides - the Devoid Cube offers only Wastes ("the only basic land you can freely add to your deck is Wastes", its
    page), the Spooty Peasant Cube snow basics; anything on the board that isn't a basic land is left out. A cube with no basic
    land on its board gets the five ordinary ones and Wastes."""
    out = {}
    names = cube if isinstance(cube, list) else (getattr(cube, "basics", None) or ())     # a Cube, or a list of the basics' names
    for name in names:
        base = name.lower().replace("snow-covered ", "").strip()
        if base in BASIC_BY_NAME and BASIC_BY_NAME[base] not in out:
            out[BASIC_BY_NAME[base]] = name
    return out or dict(BASIC_NAME)


# ---- fetching and the cache -------------------------------------------------------------------------------------------------------
def cache_path(cube_id, folder=None):
    if folder is None:
        import paths
        folder = os.path.join(paths.cache_dir(), "cubes")
    return os.path.join(folder, f"{cube_id}.json")


def fetch(cube_id, session=None, timeout=FETCH_TIMEOUT):
    """Cube Cobra's JSON for this cube (a dict). Raises CubeError with a reason a player can read."""
    if os.environ.get("MANTICORE_OFFLINE"):
        raise CubeError("Offline (MANTICORE_OFFLINE): Cube Cobra wasn't asked.")
    try:
        import requests
    except ImportError:                                          # pragma: no cover - requests is in requirements.txt
        raise CubeError("The requests package is missing.")
    s = session or requests.Session()
    try:
        r = s.get(CUBE_JSON_URL.format(id=cube_id), timeout=timeout, stream=True,
                  headers={"User-Agent": user_agent(), "Accept": "application/json"})
    except Exception as e:
        raise CubeError(f"Couldn't reach Cube Cobra ({type(e).__name__}).")
    if r.status_code == 404:
        raise CubeError(f"Cube Cobra has no cube called \"{cube_id}\" (or it's private).")
    if r.status_code == 429:
        raise CubeError("Cube Cobra says too many requests - try again in a minute.")
    if r.status_code != 200:
        raise CubeError(f"Cube Cobra answered {r.status_code}.")
    body = b""
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) > MAX_BYTES:
            raise CubeError("That cube's data is too big to be a cube.")
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise CubeError("Cube Cobra's answer wasn't readable.")


def save_cache(cube_id, data, folder=None, now=None):
    path = cache_path(cube_id, folder)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched": now if now is not None else time.time(), "cube": data}, f)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def read_cache(cube_id, folder=None):
    """(data, fetched time) from the cache, or (None, None)."""
    try:
        with open(cache_path(cube_id, folder), encoding="utf-8") as f:
            kept = json.load(f)
        if isinstance(kept, dict) and isinstance(kept.get("cube"), dict):
            return kept["cube"], kept.get("fetched")
    except (OSError, ValueError):
        pass
    return None, None


def load(ref, refresh=False, session=None, folder=None, stock_folder=None):
    """(Cube, note). The kept copy unless `refresh`; a failed refresh falls back to the kept copy and says so in the note.
    Raises CubeError when there is neither. Round CD3: a stock cube is always its bundled list (stock_cubes/), refresh or not -
    the network is used for it only when the bundled file is missing (a broken install), and the note says so."""
    cube_id = parse_ref(ref)
    st = stock_entry(cube_id)
    if st is not None:
        data, fetched = read_stock(st.key, stock_folder)
        if data is not None:
            cube = from_cubecobra(data, st.key)
            return cube._replace(fetched=fetched, style=st.style, rules=tuple(st.rules), stock=True), ""
        cube, note = _load_kept(st.key, refresh, session, folder)
        return (cube._replace(style=st.style, rules=tuple(st.rules)),
                (note + " " if note else "") + "The built-in list is missing from this copy of Manticore; Cube Cobra's was used.")
    return _load_kept(cube_id, refresh, session, folder)


def _load_kept(cube_id, refresh=False, session=None, folder=None):
    data, fetched = (None, None) if refresh else read_cache(cube_id, folder)
    note = ""
    if data is None:
        try:
            data = fetch(cube_id, session=session)
            fetched = time.time()
            save_cache(cube_id, data, folder, fetched)
        except CubeError as e:
            data, fetched = read_cache(cube_id, folder)
            if data is None:
                raise
            note = f"{e} Using the copy kept on this computer."
    cube = from_cubecobra(data, cube_id)
    return cube._replace(fetched=fetched), note


# ---- what Forge can play -------------------------------------------------------------------------------------------------------
def check_forge(cards, script_text):
    """ForgeCheck(playable, missing, ai_flagged) for these CubeCards. script_text(name) -> the card's Forge script, or None when
    Forge has none. Every copy stays: a cube may hold a card twice on purpose (Commander Cube (cm) has two of each fetch land, two
    Cultivate ... - 13 doubles on 9 Oct), and 903.13f(2) lets a deck play every copy drafted. Each name is looked up once."""
    playable, missing, flagged, known = [], [], [], {}
    for c in cards:
        key = c.name.lower()
        if key not in known:
            known[key] = script_text(c.name)
            if known[key] is None:
                missing.append(c.name)
            elif AI_FLAG.search(known[key]):
                flagged.append(c.name)
        if known[key] is not None:
            playable.append(c)
    return ForgeCheck(tuple(playable), tuple(missing), frozenset(flagged))


def forge_script_lookup(runtime=None):
    """script_text(name) against this computer's Forge (card_check.find_script, with Secret Lair flavor names)."""
    import card_check as cc
    import forge_client as fc
    rt = runtime or fc.DEFAULT_RUNTIME
    root = os.path.join(rt, "res", "cardsfolder")

    def script_text(name):
        text = cc.find_script(root, name)
        if text is None:
            real = cc.flavor_names(rt).get(name.lower())
            if real:
                text = cc.find_script(root, real)
        if text is None and " // " in name:
            text = cc.find_script(root, name.split(" // ")[0])
        return text
    return script_text
