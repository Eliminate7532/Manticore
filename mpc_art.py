# SPDX-License-Identifier: GPL-3.0-or-later
"""
mpc_art.py - Round ALT2: card pictures from MPC Autofill, chosen one at a time in the Card art picker.

MPC Autofill (https://mpcfill.com, free software under the GPL v3: github.com/chilli-axe/mpc-autofill) indexes card renders
that its contributors keep in their own Google Drive folders. The picker's "MPC Autofill" tab asks its server for ONE card's
renders (a search, then their details), shows Google Drive's small thumbnails of them, and downloads only the one you pick.
Nothing here runs until that tab is opened, and no picture is ever bundled with the program or sent anywhere: each belongs
to whoever made it, and stays in this computer's card cache.

MPC pictures are made for printing, so they carry a bleed: 2.72 x 3.70 inches for a 2.48 x 3.46 inch card, 0.12 inch of
extra picture on every side. bleed_box() finds it by the picture's shape and it is cut off, so the card sits like any other.

A choice is a card_data ArtKey whose set code is MPC_SET ("_mpc": the underscore keeps it apart from every Scryfall set) and
whose "collector number" is the Google Drive file id. The deck file keeps it as a comment line, "# art: Sol Ring = mpc:<id>"
(deck_library.set_mpc_art), so the card lines still round-trip with Moxfield.

Politeness: requests to the MPC Autofill server are at least MIN_INTERVAL apart, a card's search is kept for SEARCH_TTL
(cache/mpc/searches.json) and the list of sources for SOURCES_TTL, and every request names the program (USER_AGENT).
MANTICORE_MPC_BACKEND points it at another server (tests, or a self-hosted MPC Autofill).
"""
import hashlib
import io
import json
import os
import re
import string
import threading
import time
from pathlib import Path

MPC_SET = "_mpc"
BACKEND = (os.environ.get("MANTICORE_MPC_BACKEND") or "https://mpcfill.com").rstrip("/")
SITE = "https://mpcfill.com"
MIN_INTERVAL = 1.0                    # seconds between two requests to the MPC Autofill server
SEARCH_TTL = 7 * 24 * 3600            # a card's search results are kept a week
SOURCES_TTL = 24 * 3600               # the list of sources, a day
MAX_RESULTS = 400                     # renders shown per card (Island has thousands; /2/cards/ takes at most 1000)
TIMEOUT = 15
PX = {"small": 400, "medium": 800, "normal": 1000, "large": 1400}    # Google Drive thumbnail size (the longer side) per size
LANGUAGES = ["EN"]

BLEED_ASPECT = 2.72 / 3.70            # 0.735: a print file with bleed
CARD_ASPECT = 2.48 / 3.46             # 0.717: the card itself (Scryfall's 488 x 680 is 0.718)
CUT_X = 0.12 / 2.72                   # the bleed's share of the width, on each side
CUT_Y = 0.12 / 3.70                   # ... and of the height

_ID = re.compile(r"[A-Za-z0-9_-]{10,128}")


def user_agent():
    try:
        import version
        v = version.VERSION
    except Exception:
        v = "?"
    return f"Manticore/{v} (+https://github.com/Eliminate7532/Manticore)"


def valid_id(drive_id):
    return isinstance(drive_id, str) and bool(_ID.fullmatch(drive_id))


def is_mpc(key):
    """True for a picture key that names an MPC Autofill picture (an ArtKey with set code MPC_SET)."""
    return isinstance(key, tuple) and getattr(key, "set_code", None) == MPC_SET and bool(getattr(key, "collector_no", None))


def is_mpc_printing(printing):
    """True for a deck's (set, number) printing that is an MPC Autofill picture."""
    return bool(printing) and len(printing) == 2 and printing[0] == MPC_SET and bool(printing[1])


def file_part(drive_id):
    """A cache file name part for a Drive id: readable, and safe on a case-insensitive disk (ids differ by case alone)."""
    safe = "".join(c if c.isalnum() else "_" for c in drive_id.lower())[:40]
    return f"mpc_{safe}_{hashlib.sha1(drive_id.encode('utf-8')).hexdigest()[:8]}"


def query_for(name):
    """What to search MPC Autofill for: a double-faced card's front face ("Delver of Secrets // Insectile Aberration")."""
    return re.split(r"\s+//?\s+", (name or "").strip())[0]


def searchable(text):
    """MPC Autofill's own to_searchable(): lower case, no bracketed text, hyphens as spaces, no punctuation or digits."""
    s = (text or "").lower()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = s.replace("-", " ").replace("’", "'")
    s = s.translate(str.maketrans("", "", string.punctuation + string.digits))
    return " ".join(s.split())


def thumbnail_url(drive_id, px):
    """Google Drive's thumbnail of a file, at most px on its longer side (MPC Autofill's own small/medium pictures)."""
    return f"https://drive.google.com/thumbnail?sz=w{px}-h{px}&id={drive_id}"


def bleed_box(w, h):
    """(x, y, w, h) of the card inside a print file with bleed, or None when the picture is already card-shaped (or isn't
    either shape: it is then used as it is)."""
    if w <= 0 or h <= 0:
        return None
    aspect = w / h
    if abs(aspect - BLEED_ASPECT) > 0.012 or abs(aspect - BLEED_ASPECT) >= abs(aspect - CARD_ASPECT):
        return None
    dx, dy = round(w * CUT_X), round(h * CUT_Y)
    return dx, dy, w - 2 * dx, h - 2 * dy


def save_card_picture(data, dest):
    """Decode an image (bytes), cut off its bleed, and save it at `dest` (.jpg, written to a .tmp first). True when saved."""
    import pygame                                   # only needed here; the rest of this module works without it
    surf = pygame.image.load(io.BytesIO(data), "picture.jpg")
    box = bleed_box(*surf.get_size())
    if box:
        surf = surf.subsurface(pygame.Rect(box)).copy()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".tmp.jpg")    # pygame picks the format from the extension
    pygame.image.save(surf, str(tmp))
    os.replace(tmp, dest)
    return True


def _compact(card):
    """What the picker keeps of one /2/cards/ result."""
    return {
        "id": card.get("identifier"),
        "name": card.get("name") or "",
        "source": card.get("sourceName") or card.get("sourceVerbose") or card.get("source") or "",
        "dpi": int(card.get("dpi") or 0),
        "size": int(card.get("size") or 0),
        "tags": list(card.get("tags") or []),
        "ext": card.get("extension") or "",
        "lang": card.get("language") or "",
        "q": card.get("searchq") or searchable(card.get("name") or ""),
    }


def same_card(found_q, query):
    """True when a search hit is this card and not merely a card whose name has all its words ("Island Sanctuary" for
    "Island"): every word of the hit's name is a word of the card's name."""
    want = set(searchable(query).split())
    got = set((found_q or "").split())
    return bool(got) and got <= want


class MpcClient:
    """Searches and picture downloads. search() and fetch_image() make network requests (call them from the art loader's
    threads); peek() and card() only read what's already kept and are safe from the GUI thread."""

    def __init__(self, cache_dir, session=None, backend=None, clock=time.monotonic, sleep=time.sleep):
        self.dir = Path(cache_dir) / "mpc"
        self.backend = (backend or BACKEND).rstrip("/")
        self._session = session
        self._clock, self._sleep = clock, sleep
        self._lock = threading.RLock()              # the kept searches
        self._net = threading.Lock()                # one request to the MPC Autofill server at a time
        self._last = None
        self._searches = None                       # name lower -> {"t": time, "cards": [...]}
        self._sources = None                        # {"t": time, "pks": [...]}
        self.last_error = None

    # ---- the session, made when first needed
    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": user_agent(), "Accept": "application/json;q=0.9,*/*;q=0.8"})
        return self._session

    # ---- kept results
    def _file(self, name):
        return self.dir / name

    def _load(self, name):
        try:
            with open(self._file(name), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, name, data):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file(name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self._file(name))
        except OSError:
            pass                                    # a cache that can't be written only costs another search later

    def _kept(self):
        with self._lock:
            if self._searches is None:
                self._searches = self._load("searches.json")
            return self._searches

    def peek(self, name):
        """The kept results for this card ([] when MPC Autofill has none), or None when it hasn't been searched (or the
        search is older than SEARCH_TTL)."""
        key = query_for(name).lower()
        with self._lock:
            got = self._kept().get(key)
            if not got or time.time() - got.get("t", 0) > SEARCH_TTL:
                return None
            return list(got.get("cards") or [])

    def card(self, drive_id):
        """The kept details of one picture (from any kept search), or None."""
        with self._lock:
            for got in self._kept().values():
                for c in got.get("cards") or []:
                    if c.get("id") == drive_id:
                        return dict(c)
        return None

    # ---- the network
    def _wait_turn(self):
        if self._last is not None:
            wait = MIN_INTERVAL - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _request(self, method, path, body=None):
        """(status, json or None). Raises nothing: a network problem is status 0 with last_error set."""
        with self._net:
            self._wait_turn()
            try:
                if method == "GET":
                    r = self.session.get(self.backend + path, timeout=TIMEOUT)
                else:
                    r = self.session.post(self.backend + path, json=body, timeout=TIMEOUT)
            except Exception as e:                  # requests' own errors, and anything a broken proxy throws
                self.last_error = f"MPC Autofill can't be reached ({type(e).__name__})"
                return 0, None
        try:
            data = r.json()
        except Exception:
            data = None
        if r.status_code != 200:
            msg = (data or {}).get("message") if isinstance(data, dict) else None
            self.last_error = f"MPC Autofill answered HTTP {r.status_code}" + (f": {msg}" if msg else "")
        return r.status_code, data

    def sources(self):
        """Every source's pk (MPC Autofill searches only the sources it's given). Kept a day. None when unreachable."""
        with self._lock:
            if self._sources is None:
                self._sources = self._load("sources.json")
            got = self._sources
            if got.get("pks") and time.time() - got.get("t", 0) <= SOURCES_TTL:
                return list(got["pks"])
        status, data = self._request("GET", "/2/sources/")
        results = (data or {}).get("results") if status == 200 and isinstance(data, dict) else None
        if not isinstance(results, dict):
            if status == 200:
                self.last_error = "MPC Autofill sent a list of sources this version doesn't understand"
            return None
        pks = [s.get("pk") for s in results.values() if isinstance(s, dict) and isinstance(s.get("pk"), int)
               and (s.get("sourceType") in (None, "Google Drive"))]
        with self._lock:
            self._sources = {"t": time.time(), "pks": pks}
            self._save("sources.json", self._sources)
        return pks

    @staticmethod
    def settings(pks):
        return {"filterSettings": {"excludesTags": [], "includesTags": [], "languages": list(LANGUAGES),
                                   "maximumDPI": 1500, "maximumSize": 30, "minimumDPI": 0},
                "searchTypeSettings": {"filterCardbacks": False, "fuzzySearch": False},
                "sourceSettings": {"sources": [[pk, True] for pk in pks]}}

    def _search_ids(self, query, pks):
        """The Drive ids MPC Autofill finds for `query`, best first, or None. /2/editorSearch/ (what mpcfill.com serves today,
        kept there for clients like this one); /3/editorSearch/ (the newer form) when the old one is gone."""
        body = {"queries": [{"query": query, "cardType": "CARD"}], "searchSettings": self.settings(pks)}
        status, data = self._request("POST", "/2/editorSearch/", body)
        if status == 200 and isinstance(data, dict) and isinstance(data.get("results"), dict):
            res = data["results"]
            got = res.get(query) if query in res else next(iter(res.values()), {})
            ids = got.get("CARD") if isinstance(got, dict) else None
            return [i for i in ids or [] if valid_id(i)]
        if status not in (404, 405, 410):
            return None
        body = {"queries": {"q": {"query": query, "cardType": "CARD"}}, "searchSettings": self.settings(pks)}
        status, data = self._request("POST", "/3/editorSearch/", body)
        if status == 200 and isinstance(data, dict) and isinstance(data.get("results"), dict):
            ids = data["results"].get("q")
            return [i for i in ids or [] if valid_id(i)] if isinstance(ids, list) else []
        return None

    def search(self, name):
        """Search MPC Autofill for this card: a list of compact results (best first; [] when there are none), kept for
        SEARCH_TTL. None when the server can't be reached or answers something unexpected (last_error says what)."""
        query = query_for(name)
        key = query.lower()
        kept = self.peek(name)
        if kept is not None:
            return kept
        if not query:
            return []
        pks = self.sources()
        if pks is None:
            return None
        ids = self._search_ids(query, pks)
        if ids is None:
            return None
        ids = list(dict.fromkeys(ids))[:MAX_RESULTS]
        cards = []
        if ids:
            status, data = self._request("POST", "/2/cards/", {"cardIdentifiers": ids})
            results = (data or {}).get("results") if status == 200 and isinstance(data, dict) else None
            if not isinstance(results, dict):
                return None
            for i in ids:
                c = results.get(i)
                if isinstance(c, dict) and c.get("sourceType") in (None, "Google Drive") and valid_id(c.get("identifier")):
                    cards.append(_compact(c))
            exact = [c for c in cards if same_card(c["q"], query)]
            cards = exact or cards
        with self._lock:
            kept = self._kept()
            kept[key] = {"t": time.time(), "cards": cards}
            for k in [k for k, v in kept.items() if time.time() - v.get("t", 0) > SEARCH_TTL]:
                kept.pop(k, None)
            self._save("searches.json", kept)
        self.last_error = None
        return list(cards)

    def fetch_image(self, drive_id, dest, size="normal"):
        """Download one picture (Google Drive's thumbnail, PX[size] on its longer side), cut off the bleed, save it at `dest`.
        True when saved; False with last_error set. Not rate limited: Google serves these like any web page's pictures."""
        if not valid_id(drive_id):
            self.last_error = "not an MPC Autofill picture id"
            return False
        try:
            r = self.session.get(thumbnail_url(drive_id, PX.get(size, PX["normal"])), timeout=TIMEOUT,
                                 headers={"Accept": "image/*,*/*;q=0.8"})
        except Exception as e:
            self.last_error = f"Google Drive can't be reached ({type(e).__name__})"
            return False
        kind = (getattr(r, "headers", None) or {}).get("Content-Type") or "image/"
        if r.status_code != 200 or not kind.startswith("image/"):
            self.last_error = (f"the picture isn't available (HTTP {r.status_code})" if r.status_code != 200
                               else "the picture isn't available (Google Drive sent a page, not a picture)")
            return False
        try:
            return save_card_picture(r.content, dest)
        except Exception as e:                       # a broken picture (pygame.error) or a disk problem
            self.last_error = f"the picture couldn't be read or saved ({type(e).__name__})"
            return False
