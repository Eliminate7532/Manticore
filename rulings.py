# SPDX-License-Identifier: GPL-3.0-or-later
"""Round JDG1: a card's official rulings, from Scryfall, kept on disk.

Scryfall's card object carries a `rulings_uri` (https://api.scryfall.com/cards/<id>/rulings); the answer is a list of
{"object": "ruling", "oracle_id", "source": "wotc" | "scryfall", "published_at": "YYYY-MM-DD", "comment"} (checked live on
10 Oct 2026: Thassa's Oracle 6, Delver of Secrets 1, Doubling Season 5, every one "wotc").

Never on the window's thread: get() only reads memory and the cache file and, when a fetch is needed, starts one daemon thread
for that card (at most one per card at a time). No pygame here.
Cached in <cache>/rulings/<scryfall id>.json and fetched again after CACHE_DAYS (rulings change rarely - an estimate). With no
network (MANTICORE_OFFLINE, or the fetch failed to connect) a cached list is still shown; without one the status says so.
A failed fetch writes one crashlog note per run and never opens a window.
"""
import json
import os
import re
import threading
import time

import paths

API = "https://api.scryfall.com"
CACHE_DAYS = 30
RETRY_SECONDS = 60                     # a failed card is not asked again for a minute
POLITE_SECONDS = 0.1                   # card_data's own gap between Scryfall calls

# what get() says
OK = "ok"                              # rulings (maybe none) from memory, the cache or the network
LOADING = "loading"                    # being fetched now
OFFLINE = "offline"                    # no network and nothing cached
FAILED = "failed"                      # Scryfall answered, but not with rulings (or sent something unreadable)
NO_CARD = "no_card"                    # no Scryfall card to ask about (a token, a card whose data isn't loaded yet)

_lock = threading.Lock()
_mem = {}                              # scryfall id -> (fetched_at, [rulings])
_jobs = set()                          # ids being fetched
_failed = {}                           # id -> (until, status)
_noted = False                         # one crashlog note per run
_version = 0


def version():
    """Goes up whenever a fetch ends, so a window can tell something arrived."""
    return _version


def folder():
    return os.path.join(paths.cache_dir(), "rulings")


def _safe_id(card_id):
    return re.sub(r"[^0-9A-Za-z-]", "_", str(card_id))[:64]


def cache_path(card_id):
    return os.path.join(folder(), _safe_id(card_id) + ".json")


def offline():
    return bool(os.environ.get("MANTICORE_OFFLINE"))


def newest_first(rulings):
    """The list sorted by date, newest first; Scryfall's own order kept within one date."""
    rows = [r for r in rulings if isinstance(r, dict) and r.get("comment")]
    return sorted(rows, key=lambda r: str(r.get("published_at") or ""), reverse=True)


def read_cache(card_id):
    """(fetched_at, rulings) from the file, or None."""
    try:
        with open(cache_path(card_id), encoding="utf-8") as f:
            data = json.load(f)
        return float(data["fetched_at"]), list(data["rulings"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cache(card_id, rulings, now=None):
    os.makedirs(folder(), exist_ok=True)
    path = cache_path(card_id)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"            # two writers never share a temporary file
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": now if now is not None else time.time(), "rulings": rulings}, f)
    os.replace(tmp, path)


def _http_fetch(uri):
    """(status, rulings) for one card - OK with the list, OFFLINE when Scryfall can't be reached, FAILED otherwise."""
    import requests
    import card_data
    try:
        resp = requests.get(uri, headers=card_data.HEADERS, timeout=10)
    except requests.RequestException as e:
        return OFFLINE, f"{type(e).__name__}: {e}"
    finally:
        time.sleep(POLITE_SECONDS)
    if resp.status_code != 200:
        return FAILED, f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        return OK, [r for r in body["data"] if isinstance(r, dict)]
    except (ValueError, KeyError, TypeError) as e:
        return FAILED, f"unreadable answer ({type(e).__name__})"


FETCH = _http_fetch                    # tests put a stand-in here


def _note(text):
    global _noted
    if _noted:
        return
    _noted = True
    try:
        import crashlog
        crashlog.note("Rulings not loaded", text)
    except Exception:
        pass


def _run(card_id, uri, name):
    global _version
    status, result = OFFLINE, "not started"
    try:
        status, result = FETCH(uri)
    except Exception as e:                                   # never let a fetch thread die loudly
        status, result = FAILED, f"{type(e).__name__}: {e}"
    if status == OK:                                         # on disk before the job counts as done
        try:
            write_cache(card_id, result)
        except OSError as e:
            _note(f"The rulings of {name} could not be saved: {e}")
    else:
        _note(f"The rulings of {name} were not loaded from Scryfall: {result}")
    with _lock:
        if status == OK:
            _mem[card_id] = (time.time(), result)
            _failed.pop(card_id, None)
        else:
            _failed[card_id] = (time.time() + RETRY_SECONDS, status)
        _jobs.discard(card_id)
        _version += 1


def _start(card_id, uri, name):
    with _lock:
        if card_id in _jobs:
            return None
        _jobs.add(card_id)
    t = threading.Thread(target=_run, args=(card_id, uri, name), name="rulings", daemon=True)
    t.start()
    return t


def get(card, now=None):
    """(status, rulings newest first) for a Scryfall card object (card_data's). Never waits on the network."""
    if not isinstance(card, dict) or not card.get("id"):
        return NO_CARD, []
    now = time.time() if now is None else now
    cid = str(card["id"])
    uri = card.get("rulings_uri") or f"{API}/cards/{cid}/rulings"
    name = card.get("name") or cid
    with _lock:
        have = _mem.get(cid)
        busy = cid in _jobs
        failed = _failed.get(cid)
    if have is None:
        have = read_cache(cid)
        if have is not None:
            with _lock:
                _mem.setdefault(cid, have)
    if have is not None:
        if now - have[0] > CACHE_DAYS * 86400 and not busy and not offline() and not (failed and failed[0] > now):
            _start(cid, uri, name)                           # old: shown as it is while a newer list is fetched
        return OK, newest_first(have[1])
    if busy:
        return LOADING, []
    if offline():
        return OFFLINE, []
    if failed and failed[0] > now:
        return failed[1], []
    _start(cid, uri, name)
    return LOADING, []


def reset():
    """Forget everything kept in memory (tests)."""
    global _noted, _version
    with _lock:
        _mem.clear()
        _jobs.clear()
        _failed.clear()
        _noted = False
        _version += 1
