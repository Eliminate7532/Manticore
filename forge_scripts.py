# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_scripts.py - card rules data taken from the Forge project's card scripts.

Forge (https://github.com/Card-Forge/forge, GPL-3.0) describes every Magic card in a small text
file. For example Wooded Foothills is

    Name:Wooded Foothills
    ManaCost:no cost
    Types:Land
    A:AB$ ChangeZone | Cost$ T PayLife<1> Sac<1/CARDNAME> | Origin$ Library | Destination$ Battlefield | ChangeType$ Mountain,Forest | ...

Instead of guessing what a card does by pattern-matching its Oracle text, this module downloads
the card's Forge script (once, then it is cached on disk) and parses it into plain Python objects:
abilities and their costs, triggers, replacement effects ("enters tapped unless..."), and Forge's
small "valid card" expression language (e.g. "Creature.Legendary+YouCtrl").

Only the parts of Forge's script language that this simulator uses are understood. Anything
else is reported as unsupported (ForgeUnsupported) so callers can fall back or tell the player.

Licence note: the scripts are part of Forge and therefore GPL-3.0. They are downloaded to the
user's own cache folder for personal use and are not redistributed with this project. Think
about the licence before ever shipping the scripts (or code derived from Forge) to other people.
"""
import concurrent.futures
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests

RAW_URL = "https://raw.githubusercontent.com/Card-Forge/forge/master/forge-gui/res/cardsfolder/{letter}/{file}.txt"
HEADERS = {"User-Agent": "CommanderSim/0.1 (personal hobby project)"}
MISSING_RETRY_SECONDS = 7 * 24 * 3600      # try a script that 404'd again after a week

SUPERTYPES = {"Basic", "Legendary", "Snow", "World", "Ongoing"}
CARD_TYPES = {"Artifact", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery",
              "Battle", "Kindred", "Tribal", "Dungeon", "Plane", "Phenomenon", "Scheme", "Vanguard",
              "Conspiracy", "Attraction"}
COLOR_WORDS = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
BASIC_LAND_MANA = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


class ForgeUnsupported(Exception):
    """A script uses something this simulator does not model."""


# ---------------------------------------------------------------------------
# parsing a script
# ---------------------------------------------------------------------------
def parse_params(text):
    """'AB$ Mana | Cost$ T | Produced$ G' -> {'AB': 'Mana', 'Cost': 'T', 'Produced': 'G'}."""
    params = {}
    for part in text.split(" | "):
        key, sep, value = part.partition("$")
        if sep:
            params[key.strip()] = value.strip()
    return params


@dataclass
class ForgeFace:
    name: str = ""
    mana_cost: str = ""                 # "G U" (Forge writes symbols separated by spaces), or "no cost"
    types: list = field(default_factory=list)      # ["Legendary", "Creature", "Human", "Druid"]
    pt: str = ""
    keywords: list = field(default_factory=list)   # K: lines
    abilities: list = field(default_factory=list)  # A: lines as parameter dicts
    triggers: list = field(default_factory=list)   # T: lines
    replacements: list = field(default_factory=list)   # R: lines
    statics: list = field(default_factory=list)    # S: lines
    svars: dict = field(default_factory=dict)      # name -> raw text
    oracle: str = ""
    alternate_mode: str = ""

    @property
    def card_types(self):
        return [t for t in self.types if t in CARD_TYPES]

    @property
    def is_land(self):
        return "Land" in self.types

    def svar_params(self, name):
        """Parse an SVar that holds an ability ('DB$ DealDamage | NumDmg$ 1 | ...'). None if missing."""
        raw = self.svars.get(name)
        return parse_params(raw) if raw and "$" in raw else None


@dataclass
class ForgeCard:
    faces: list = field(default_factory=list)

    @property
    def name(self):
        return self.faces[0].name if self.faces else ""

    def face(self, index=0):
        return self.faces[min(index, len(self.faces) - 1)] if self.faces else None


def parse_script(text):
    """Parse the text of one Forge card script into a ForgeCard (one ForgeFace per card face)."""
    card = ForgeCard()
    face = ForgeFace()
    started = False
    for raw in text.lstrip("\ufeff").splitlines():      # tolerate a UTF-8 BOM and Windows line endings
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "ALTERNATE":
            card.faces.append(face)
            face = ForgeFace()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        started = True
        if key == "Name":
            face.name = value
        elif key == "ManaCost":
            face.mana_cost = value
        elif key == "Types":
            face.types = value.split()
        elif key == "PT":
            face.pt = value
        elif key == "K":
            face.keywords.append(value)
        elif key == "A":
            face.abilities.append(parse_params(value))
        elif key == "T":
            face.triggers.append(parse_params(value))
        elif key == "R":
            face.replacements.append(parse_params(value))
        elif key == "S":
            face.statics.append(parse_params(value))
        elif key == "SVar":
            name, _, body = value.partition(":")
            face.svars[name] = body
        elif key == "Oracle":
            face.oracle = value.replace("\\n", "\n")
        elif key == "AlternateMode":
            face.alternate_mode = value
    if not started:
        raise ValueError("not a Forge card script")
    card.faces.append(face)
    if not card.faces[0].name:
        raise ValueError("Forge script has no Name line")
    return card


# ---------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------
@dataclass
class ForgeCost:
    tap: bool = False
    mana: dict = field(default_factory=dict)        # {"generic": 2, "U": 1}
    life: int = 0
    sac_self: bool = False
    sac_other: tuple = ()                           # (count, valid expression)
    tap_other: tuple = ()                           # (count, valid expression): "tap an untapped creature you control"
    exile_self_from_hand: bool = False
    discard_self: bool = False
    unsupported: list = field(default_factory=list)


_COST_TOKEN = re.compile(r"^(\w+)<(.*)>$")


def parse_cost(text):
    """Parse a Forge Cost$ value such as 'T PayLife<1> Sac<1/CARDNAME>' or '1 U T'."""
    cost = ForgeCost()
    for token in (text or "").split():
        if token == "T":
            cost.tap = True
        elif token.isdigit():
            cost.mana["generic"] = cost.mana.get("generic", 0) + int(token)
        elif token in ("W", "U", "B", "R", "G", "C"):
            cost.mana[token] = cost.mana.get(token, 0) + 1
        else:
            m = _COST_TOKEN.match(token)
            if not m:
                cost.unsupported.append(token)
                continue
            kind, args = m.groups()
            count, _, what = args.partition("/")
            try:
                n = int(count)
            except ValueError:
                cost.unsupported.append(token)
                continue
            if kind == "PayLife":
                cost.life += n
            elif kind == "Sac":
                if what == "CARDNAME":
                    cost.sac_self = True
                else:
                    cost.sac_other = (n, what)
            elif kind == "tapXType":
                cost.tap_other = (n, what)
            elif kind == "ExileFromHand" and what == "CARDNAME":
                cost.exile_self_from_hand = True
            elif kind == "Discard" and what == "CARDNAME":
                cost.discard_self = True
            else:
                cost.unsupported.append(token)
    return cost


# ---------------------------------------------------------------------------
# small helpers of Forge's script language
# ---------------------------------------------------------------------------
_COMPARE = re.compile(r"^(GT|GE|LT|LE|EQ|NE)(-?\d+)$")


def compare(value, spec):
    """Forge compare strings: 'GT2' means value > 2, 'GE1' value >= 1, 'EQ0', 'NE1', 'LT2', 'LE3'."""
    m = _COMPARE.match((spec or "GE1").strip())
    if not m:
        raise ForgeUnsupported(f"comparison '{spec}'")
    op, n = m.group(1), int(m.group(2))
    return {"GT": value > n, "GE": value >= n, "LT": value < n, "LE": value <= n,
            "EQ": value == n, "NE": value != n}[op]


def type_words(type_line):
    """'Legendary Creature — Human Druid' or 'Land // Land' -> {'Legendary','Creature','Human','Druid','Land'}."""
    return {w for w in re.split(r"[\s—–\-/]+", type_line or "") if w}


# Capitalised qualifiers that are NOT card types, so "not in the type list" would be the wrong reading.
_NON_TYPE_QUALIFIERS = {"Token", "Historic", "ChosenType", "Chosen", "Enchanted", "Equipped", "Attacking",
                        "Blocking", "Modified", "Imprinted", "IsImprinted", "IsRemembered", "Remembered",
                        "Named", "Sacrificed", "Targeted", "Triggered", "Untapped", "Tapped"}


def matches_valid(expr, types, colors=(), controller="you", is_self=False, tapped=False):
    """Does a card match a Forge 'valid' expression such as 'Creature.Legendary+YouCtrl,Land.Basic'?

    `types` is a set of type words (see type_words), `colors` a set of 'W','U','B','R','G'.
    A comma separates alternatives; an alternative is 'Base' or 'Base.Qualifier+Qualifier'.
    Type words (Legendary, Goblin, Forest...) are matched against `types`. Qualifiers this module
    does not understand raise ForgeUnsupported rather than guessing.
    """
    for alt in expr.split(","):
        base, _, quals = alt.strip().partition(".")
        if base == "Defined":
            raise ForgeUnsupported("valid expression 'Defined...' needs the game context")
        if base not in ("Card", "Permanent") and base not in types:
            continue
        ok = True
        for q in filter(None, quals.replace(".", "+").split("+")):
            if q in ("YouCtrl", "YouOwn"):
                ok = ok and controller == "you"
            elif q in ("OppCtrl", "OppOwn"):
                ok = ok and controller == "opp"
            elif q == "Other":
                ok = ok and not is_self
            elif q == "Self":
                ok = ok and is_self
            elif q == "tapped":
                ok = ok and tapped
            elif q == "untapped":
                ok = ok and not tapped
            elif q == "Colorless":
                ok = ok and not colors
            elif q in ("nonColorless", "Colored"):
                ok = ok and bool(colors)
            elif q in COLOR_WORDS:
                ok = ok and COLOR_WORDS[q] in colors
            elif q.startswith("non") and q[3:] in COLOR_WORDS:
                ok = ok and COLOR_WORDS[q[3:]] not in colors
            elif q.startswith("non") and q[3:4].isupper() and q[3:].isalpha():
                ok = ok and q[3:] not in types
            elif q in _NON_TYPE_QUALIFIERS or not (q[:1].isupper() and q.isalpha()):
                raise ForgeUnsupported(f"valid qualifier '{q}'")
            else:
                ok = ok and q in types           # Legendary, Basic, Goblin, Forest ...
        if ok:
            return True
    return False


# ---------------------------------------------------------------------------
# finding and downloading scripts
# ---------------------------------------------------------------------------
def script_file_name(card_name):
    """Forge's file name for a card: 'Kinnan, Bonder Prodigy' -> 'kinnan_bonder_prodigy'.

    Double-faced and split cards are stored under both face names joined by '_'
    ('Barkchannel Pathway // Tidechannel Pathway' -> 'barkchannel_pathway_tidechannel_pathway').
    """
    faces = [f for f in re.split(r"\s+//?\s+", card_name.strip()) if f]
    parts = []
    for f in faces:
        f = f.lower().replace("æ", "ae").replace("œ", "oe").replace("ß", "ss")
        f = unicodedata.normalize("NFKD", f)                       # 'û' -> 'u' + accent, accent dropped below
        f = re.sub(r"['’]|[\u0300-\u036f]", "", f)
        parts.append(re.sub(r"[^a-z0-9]+", "_", f).strip("_"))
    return "_".join(parts)


def script_url(file_name):
    return RAW_URL.format(letter=file_name[:1], file=file_name)


class ForgeScriptStore:
    """Forge card scripts, cached in <cache_dir>/forge/.

    peek(name) never touches the network (safe on the GUI thread); fetch()/prefetch() do, and
    are meant for the background worker thread. Use the card's full Scryfall name
    ('Barkchannel Pathway // Tidechannel Pathway') so double-faced cards find their file.
    """

    OFFLINE_PAUSE_SECONDS = 60        # after a network failure, don't try again for this long

    def __init__(self, cache_dir):
        self.dir = Path(cache_dir) / "forge"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cards = {}                    # lower-case name -> ForgeCard, or None when not available
        self.last_error = None
        self._lock = threading.RLock()
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._offline_until = 0.0
        self._missing_file = self.dir / "_missing.json"
        self._missing = {}                 # file name -> time of the 404
        try:
            self._missing = json.loads(self._missing_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    # ---- reading (no network) ---------------------------------------------
    def _register(self, name, card):
        """Remember a script under the name it was asked for and under each face name."""
        self.cards[name.strip().lower()] = card
        if card is not None:
            for face in card.faces:
                if face.name:
                    self.cards.setdefault(face.name.lower(), card)

    def peek(self, name):
        """The parsed script from memory or disk, or None. Never touches the network."""
        key = name.strip().lower()
        if key in self.cards:
            return self.cards[key]
        card = self._load_from_disk(name)
        self._register(name, card)
        return card

    def _load_from_disk(self, name):
        for file_name in self._candidates(name):
            path = self.dir / f"{file_name}.txt"
            if not path.exists():
                continue
            try:
                return parse_script(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.last_error = f"Could not read cached Forge script {path.name}: {e}"
        return None

    @staticmethod
    def _candidates(name):
        """File names to try, most specific first: full name, then front face alone."""
        names = [script_file_name(name)]
        front = script_file_name(re.split(r"\s+//?\s+", name.strip())[0])
        if front not in names:
            names.append(front)
        return names

    # ---- downloading ---------------------------------------------------------
    def _is_recently_missing(self, file_name):
        when = self._missing.get(file_name)
        return when is not None and time.time() - when < MISSING_RETRY_SECONDS

    def _remember_missing(self, file_name):
        with self._lock:
            self._missing[file_name] = time.time()
            try:
                self._missing_file.write_text(json.dumps(self._missing), encoding="utf-8")
            except OSError:
                pass

    @property
    def offline(self):
        return time.time() < self._offline_until

    def fetch(self, name):
        """Download (if needed) and parse one card's script. Returns a ForgeCard or None."""
        card = self.peek(name)
        if card is None:
            card = self._download(name)
            if card is not None:
                self._register(name, card)
        return card

    def _download(self, name):
        for file_name in self._candidates(name):
            if self._is_recently_missing(file_name):
                continue
            if self.offline:
                return None
            try:
                resp = self._session.get(script_url(file_name), timeout=(5, 10))
            except requests.RequestException as e:
                self._offline_until = time.time() + self.OFFLINE_PAUSE_SECONDS
                self.last_error = f"Forge script download failed ({type(e).__name__}); rules fall back to card text."
                return None
            if resp.status_code == 404:
                self._remember_missing(file_name)
                continue
            if resp.status_code != 200:
                self._offline_until = time.time() + self.OFFLINE_PAUSE_SECONDS
                self.last_error = f"GitHub returned HTTP {resp.status_code} for the Forge script of '{name}'"
                return None
            try:
                card = parse_script(resp.text)
            except ValueError as e:
                self.last_error = f"Forge script for '{name}' could not be parsed: {e}"
                return None
            tmp = self.dir / f"{file_name}.txt.tmp"
            try:
                tmp.write_text(resp.text, encoding="utf-8")
                tmp.replace(self.dir / f"{file_name}.txt")
            except OSError as e:
                self.last_error = f"Could not save Forge script for '{name}': {e}"
            return card
        return None

    def prefetch(self, names, workers=6):
        """Download the scripts for many cards at once. Returns how many are now available."""
        names = sorted(set(names))
        todo = [n for n in names if self.peek(n) is None]
        if todo:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(self.fetch, todo))
        return sum(1 for n in names if self.peek(n) is not None)
