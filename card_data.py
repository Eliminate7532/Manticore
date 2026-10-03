# SPDX-License-Identifier: GPL-3.0-or-later
"""
card_data.py - Scryfall API card fetching and local caching layer

Failure handling: every network problem is caught and recorded in
CardDataStore.last_error (a short human-readable string) instead of being
swallowed silently. Failed lookups are remembered ("negative cache") so a
broken lookup does not hit the network again on every frame.
"""
import functools
import json
import re
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

import requests

import paths
from forge_scripts import ForgeScriptStore

# Round 28: cache_dir() is the program folder's own cache/ in portable mode (exactly as before this round),
# or the per-user local_dir()/cache in an installed copy. Nothing here creates a folder at import time any
# more - CardDataStore.__init__ makes them on first use (paths.py's own rule: only ensure_dirs() creates
# directories, and this module doesn't call that; it just needs its own four folders to exist before it
# reads or writes them).
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(paths.cache_dir())
IMAGE_CACHE_DIR = CACHE_DIR / "images"
IMAGE_CACHE_DIR_LARGE = CACHE_DIR / "images_large"          # round 26: 672x936 pictures for the big preview
IMAGE_CACHE_DIR_ART = CACHE_DIR / "images_art"               # round 26: art_crop pictures for small-card board frames
IMAGE_CACHE_DIR_SMALL = CACHE_DIR / "images_small"           # round ALT1: 146x204 pictures for the printings strip
DATA_CACHE_FILE = CACHE_DIR / "card_data.json"

SCRYFALL_API = "https://api.scryfall.com"

# Scryfall rejects requests that use the default python-requests User-Agent
# (HTTP 400, rule "generic_user_agent"). It asks for a descriptive UA and an
# Accept header. See https://scryfall.com/docs/api
HEADERS = {
    "User-Agent": "CommanderSim/0.1 (personal hobby project)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}

# After a transient failure (network down, 5xx, 429), wait this many seconds
# before trying that card again. A 404 ("no such card") is never retried
# during the same run.
RETRY_COOLDOWN_SECONDS = 30
RATE_LIMIT_COOLDOWN_SECONDS = 60


class ArtKey(NamedTuple):
    """Round ALT1: which picture of a card - (name, set code, collector number). (name, None, None) is Scryfall's default
    printing, today's behaviour; everywhere a picture is looked up a plain name str still means exactly that."""
    name: str
    set_code: Optional[str] = None
    collector_no: Optional[str] = None


def is_art_key(key):
    """True for an ArtKey - by shape, not class, so a reloaded module (the tests reload card_data) still recognises it."""
    return isinstance(key, tuple) and hasattr(key, "set_code") and hasattr(key, "collector_no")


def art_key(name, set_code=None, cn=None):
    """The key for a picture: a plain str for the default printing (so every cache key of the default stays what it was before
    ALT1), an ArtKey only when a printing is chosen."""
    if is_art_key(name):
        name, set_code, cn = name
    if set_code and cn:
        return ArtKey(name, str(set_code).lower(), str(cn))
    return name


def key_name(key):
    """The card name of a picture key (str or ArtKey)."""
    return key.name if is_art_key(key) else key


def safe_part(text):
    return "".join(c if c.isalnum() else "_" for c in str(text).lower())


def _synchronized(method):
    """Run a CardDataStore method while holding the store's lock (safe across threads)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def _front_face_name(name):
    """'Delver of Secrets // Insectile Aberration' or 'A / B' -> 'Delver of Secrets'."""
    return re.split(r"\s+//?\s+", name.strip())[0]


class CardDataStore:
    def __init__(self):
        self.cards = {}
        self._lock = threading.RLock()
        self.last_error = None     # most recent problem, for display in the GUI
        self._failed = {}          # key -> (retry_not_before_timestamp or None, message)
        self._reported = set()     # messages already printed to the console
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._printings = {}       # round ALT1: name lower -> [printing dicts] (list_printings, this session only)
        for d in (CACHE_DIR, IMAGE_CACHE_DIR, IMAGE_CACHE_DIR_LARGE, IMAGE_CACHE_DIR_ART, IMAGE_CACHE_DIR_SMALL):
            d.mkdir(parents=True, exist_ok=True)
        # Forge card scripts (rules data), cached in cache/forge/. See forge_scripts.py.
        self.forge = ForgeScriptStore(CACHE_DIR)

        if DATA_CACHE_FILE.exists():
            try:
                with open(DATA_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.cards = json.load(f)
            except (OSError, ValueError) as e:
                self._record_error(f"Could not read {DATA_CACHE_FILE.name} ({e}); starting with an empty cache")
                self.cards = {}

    # ---- helpers -------------------------------------------------------

    def _record_error(self, message):
        self.last_error = message
        if message not in self._reported:   # print each distinct problem once
            self._reported.add(message)
            print(f"[card_data] {message}")

    def _save(self):
        try:
            tmp = DATA_CACHE_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cards, f)
            tmp.replace(DATA_CACHE_FILE)
        except OSError as e:
            self._record_error(f"Could not save card cache: {e}")

    def _is_blocked(self, key):
        """True if this key failed recently and should not be retried yet."""
        entry = self._failed.get(key)
        if entry is None:
            return False
        retry_at, _msg = entry
        if retry_at is None:            # permanent failure (e.g. 404)
            return True
        if time.time() < retry_at:
            return True
        del self._failed[key]           # cooldown over, allow a retry
        return False

    def _fail(self, key, message, permanent=False, cooldown=None):
        if permanent:
            retry_at = None
        else:
            retry_at = time.time() + (cooldown if cooldown is not None else RETRY_COOLDOWN_SECONDS)
        self._failed[key] = (retry_at, message)
        self._record_error(message)

    # ---- public API ----------------------------------------------------

    def peek_card(self, name):
        """Cached card data or None. Never touches the network, so it is safe to call every frame."""
        return self.cards.get(name.strip().lower())

    def _forge_name(self, name):
        """The name Forge files use: Scryfall's full name ('A // B' for double-faced cards) if known."""
        info = self.cards.get(name.strip().lower())
        return info.get("name", name) if info else name

    def peek_forge(self, name):
        """The parsed Forge script (forge_scripts.ForgeCard) or None. Never touches the network."""
        return self.forge.peek(self._forge_name(name))

    def prefetch_forge(self, names):
        """Download the Forge scripts for many cards (after prefetch_cards, so names are canonical).

        Returns (available, total). Problems are left in self.forge.last_error.
        """
        canonical = sorted({self._forge_name(n) for n in names})
        return self.forge.prefetch(canonical), len(canonical)

    @_synchronized
    def prefetch_cards(self, names):
        """Fetch card data for many names at once (75 per request) instead of one request per card.

        Names are looked up by their front face, which Scryfall's batch endpoint
        accepts for double-faced cards. Anything the batch misses falls back to the
        one-at-a-time lookup. Returns the number of cards newly cached.
        """
        need = sorted({n for n in names
                       if n.strip().lower() not in self.cards
                       and not self._is_blocked(n.strip().lower())})
        if not need:
            return 0
        before = len(self.cards)

        by_front = {}
        for n in need:
            by_front.setdefault(_front_face_name(n).lower(), []).append(n)
        fronts = list(by_front)

        for i in range(0, len(fronts), 75):
            chunk = fronts[i:i + 75]
            try:
                resp = self._session.post(
                    f"{SCRYFALL_API}/cards/collection",
                    json={"identifiers": [{"name": f} for f in chunk]},
                    timeout=20,
                )
            except requests.RequestException as e:
                self._record_error(f"Scryfall unreachable during batch lookup: {type(e).__name__}: {e}")
                break
            time.sleep(0.1)
            if resp.status_code != 200:
                self._record_error(f"Scryfall batch lookup returned HTTP {resp.status_code}")
                break
            try:
                data = resp.json().get("data", [])
            except ValueError:
                self._record_error("Scryfall batch lookup sent invalid JSON")
                break
            for card in data:
                for requested in by_front.get(_front_face_name(card["name"]).lower(), []):
                    self.cards[requested.strip().lower()] = card

        if len(self.cards) != before:
            self._save()
        for n in need:      # slow path for anything the batch did not resolve
            if n.strip().lower() not in self.cards:
                self.get_card(n)
        return len(self.cards) - before

    @_synchronized
    def get_card(self, name):
        """Fetch card data by exact name, using cache first. Returns dict or None."""
        key = name.strip().lower()
        if key in self.cards:
            return self.cards[key]
        if self._is_blocked(key):
            return None

        try:
            resp = self._session.get(
                f"{SCRYFALL_API}/cards/named",
                params={"exact": name},
                timeout=10,
            )
        except requests.RequestException as e:
            self._fail(key, f"Scryfall unreachable for '{name}': {type(e).__name__}: {e}")
            return None
        time.sleep(0.1)  # be polite to Scryfall's rate limits (they ask for 50-100ms between calls)

        if resp.status_code != 200:
            detail = ""
            try:
                body = resp.json()
                detail = f" - {body.get('details') or body.get('rule') or ''}"
            except ValueError:
                pass
            # 429 = we are being rate limited; Scryfall asks for a 60 second wait
            # and warns that ignoring it can get an IP blocked, so back off longer.
            self._fail(
                key,
                f"Scryfall returned HTTP {resp.status_code} for '{name}'{detail[:120]}",
                permanent=(resp.status_code == 404),
                cooldown=(RATE_LIMIT_COOLDOWN_SECONDS if resp.status_code == 429 else None),
            )
            return None

        try:
            data = resp.json()
        except ValueError as e:
            self._fail(key, f"Scryfall sent invalid JSON for '{name}': {e}")
            return None

        self.cards[key] = data
        self._save()
        return data

    @staticmethod
    def _image_file(key, size="normal"):
        """The cache file for a picture. The default printing keeps the file name it always had (<safe name>.jpg); a chosen
        printing (round ALT1) is <safe name>__<set>_<safe cn>.jpg in the same folder."""
        name = key_name(key)
        safe_name = safe_part(name)
        if is_art_key(key) and key.set_code and key.collector_no:
            safe_name += f"__{safe_part(key.set_code)}_{safe_part(key.collector_no)}"
        base = {"large": IMAGE_CACHE_DIR_LARGE, "art_crop": IMAGE_CACHE_DIR_ART, "small": IMAGE_CACHE_DIR_SMALL}.get(size, IMAGE_CACHE_DIR)
        return base / f"{safe_name}.jpg"

    def peek_image_path(self, name, size="normal"):
        """Path of the already-downloaded image (at this size) or None. Never touches the network. `name` may be an ArtKey."""
        path = self._image_file(name, size)
        return str(path) if path.exists() else None

    @_synchronized
    def get_printing(self, set_code, cn):
        """Round ALT1: one printing's card object (Scryfall /cards/{set}/{cn}), cached in self.cards under "print:set/cn".
        Same politeness and negative cache as get_card; a 404 is permanent."""
        set_code = str(set_code).lower()
        key = f"print:{set_code}/{cn}"
        if key in self.cards:
            return self.cards[key]
        if self._is_blocked(key):
            return None
        try:
            resp = self._session.get(f"{SCRYFALL_API}/cards/{set_code}/{requests.utils.quote(str(cn), safe='')}", timeout=10)
        except requests.RequestException as e:
            self._fail(key, f"Scryfall unreachable for printing {set_code.upper()} {cn}: {type(e).__name__}: {e}")
            return None
        time.sleep(0.1)
        if resp.status_code != 200:
            self._fail(key, f"Scryfall returned HTTP {resp.status_code} for printing {set_code.upper()} {cn}",
                       permanent=(resp.status_code == 404),
                       cooldown=(RATE_LIMIT_COOLDOWN_SECONDS if resp.status_code == 429 else None))
            return None
        try:
            data = resp.json()
        except ValueError as e:
            self._fail(key, f"Scryfall sent invalid JSON for printing {set_code.upper()} {cn}: {e}")
            return None
        self.cards[key] = data
        self._save()
        return data

    def peek_printings(self, name):
        """The printings list_printings already found for this name this session, or None."""
        return self._printings.get(_front_face_name(name).lower())

    @_synchronized
    def list_printings(self, name, max_pages=8):
        """Round ALT1: every paper printing of a card, newest first: [{set, cn, set_name, released, image_small}] (Scryfall search
        !"name" unique=prints, following next_page). Kept in memory for this session only (the picker is its only user).
        None when Scryfall can't be reached (try again later); [] when it knows no printing."""
        front = _front_face_name(name)
        low = front.lower()
        if low in self._printings:
            return self._printings[low]
        key = "prints:" + low
        if self._is_blocked(key):
            return None
        url, params, out = f"{SCRYFALL_API}/cards/search", {"q": f'!"{front}"', "unique": "prints", "order": "released",
                                                            "include_extras": "true", "include_variations": "true"}, []
        for _page in range(max_pages):
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException as e:
                self._fail(key, f"Scryfall unreachable for the printings of '{front}': {type(e).__name__}: {e}")
                return None
            time.sleep(0.1)
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                self._fail(key, f"Scryfall returned HTTP {resp.status_code} for the printings of '{front}'",
                           cooldown=(RATE_LIMIT_COOLDOWN_SECONDS if resp.status_code == 429 else None))
                return None
            try:
                body = resp.json()
            except ValueError as e:
                self._fail(key, f"Scryfall sent invalid JSON for the printings of '{front}': {e}")
                return None
            for c in body.get("data") or []:
                if "paper" not in (c.get("games") or ["paper"]):
                    continue
                uris = c.get("image_uris") or ((c.get("card_faces") or [{}])[0].get("image_uris") or {})
                out.append({"set": (c.get("set") or "").lower(), "cn": str(c.get("collector_number") or ""),
                            "set_name": c.get("set_name") or "", "released": c.get("released_at") or "",
                            "image_small": uris.get("small"), "lang": c.get("lang") or "en"})
            if not body.get("has_more") or not body.get("next_page"):
                break
            url, params = body["next_page"], None
        self._printings[low] = out
        return out

    def _printing_image_url(self, key, size):
        """The image URL for a chosen printing, or None (the caller then uses the default printing)."""
        card = self.get_printing(key.set_code, key.collector_no)
        if not card:
            return None
        if "image_uris" in card:
            return card["image_uris"].get(size)
        faces = card.get("card_faces") or []
        if faces:
            wanted = key.name.strip().lower()
            face = next((f for f in faces if f.get("name", "").lower() == wanted), faces[0])
            return (face.get("image_uris") or {}).get(size)
        return None

    @_synchronized
    def get_image_path(self, name, size="normal"):
        """Download and cache the card image at `size` ("normal" 488x680, or "large" 672x936 for the big preview,
        round 26; "small" 146x204 for the printings strip, round ALT1), return the local file path (str) or None. Same
        polite rate limit and User-Agent as everything else in this store -- get_card() below still sleeps on its own
        cache miss. `name` may be an ArtKey (round ALT1): a printing that can't be found (bad code, offline, 404) falls back to
        the default printing's picture, never to no picture."""
        if is_art_key(name):
            key = name
            if not (key.set_code and key.collector_no):
                return self.get_image_path(key.name, size)
            local_path = self._image_file(key, size)
            if local_path.exists():
                return str(local_path)
            img_key = f"img:{size}:{key.set_code}/{key.collector_no}"
            url = None if self._is_blocked(img_key) else self._printing_image_url(key, size)
            if url and self._download(url, local_path, img_key, f"{key.name} ({key.set_code.upper()} {key.collector_no})"):
                return str(local_path)
            if not self._is_blocked(img_key):
                self._fail(img_key, f"No picture for the printing {key.set_code.upper()} {key.collector_no} of '{key.name}'; "
                                    "using the default printing")
            return self.get_image_path(key.name, size)

        card = self.get_card(name)
        if not card:
            return None

        local_path = self._image_file(name, size)
        if local_path.exists():
            return str(local_path)

        img_key = f"img:{size}:" + name.strip().lower()
        if self._is_blocked(img_key):
            return None

        image_url = None
        if "image_uris" in card:
            image_url = card["image_uris"].get(size)
        elif "card_faces" in card and card["card_faces"]:
            # a double-faced card asked for by its back-face name gets the back picture
            faces = card["card_faces"]
            wanted = name.strip().lower()
            face = next((f for f in faces if f.get("name", "").lower() == wanted), faces[0])
            image_url = face.get("image_uris", {}).get(size)

        if not image_url:
            self._fail(img_key, f"No {size} image URL in Scryfall data for '{name}'", permanent=True)
            return None
        return str(local_path) if self._download(image_url, local_path, img_key, name) else None

    def _download(self, image_url, local_path, img_key, label):
        try:
            img_resp = self._session.get(image_url, timeout=10)
        except requests.RequestException as e:
            self._fail(img_key, f"Image download failed for '{label}': {type(e).__name__}: {e}")
            return False

        if img_resp.status_code != 200:
            self._fail(img_key, f"Image download for '{label}' returned HTTP {img_resp.status_code}")
            return False

        # Write to a temp file first so an interrupted download can never
        # leave a half-written .jpg that later fails to load.
        tmp_path = local_path.with_suffix(".jpg.tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(img_resp.content)
            tmp_path.replace(local_path)
        except OSError as e:
            self._fail(img_key, f"Could not save image for '{label}': {e}")
            return False
        return True

    def load_decklist(self, lines):
        """Parse a decklist (list of 'N Card Name' strings) and prefetch data."""
        deck = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                qty, cardname = int(parts[0]), parts[1]
            else:
                qty, cardname = 1, line
            for _ in range(qty):
                deck.append(cardname)
        return deck
