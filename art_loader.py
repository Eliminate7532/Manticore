# SPDX-License-Identifier: GPL-3.0-or-later
"""
art_loader.py - loads card pictures for the Forge table without ever freezing the window.

One background thread does all the network work (card data first, then pictures), so a 100-card
deck can't stall the GUI and requests stay under Scryfall's rate limit. The GUI thread only calls
collect() / get() / preview(), which touch the local cache and never the network.
"""
import itertools
import os
import queue
import threading
import time

import pygame

from card_data import art_key, is_art_key, key_name, safe_part

SOURCE_SIZE = (244, 340)
RETRY_SECONDS = 30
IMAGES_PER_FRAME = 4
CUSTOM_EXTS = (".png", ".jpg", ".jpeg")
CUSTOM_MAX_SIDE = 4096          # round ALT1: a bigger picture in my_art/ is refused (with a toast), never scaled


def custom_stem(text):
    """How my_art/ file names and card names are compared: lower case, any run of punctuation or spaces as one "_"."""
    import re
    return re.sub(r"_+", "_", safe_part(text)).strip("_")


def scan_custom(folder):
    """Round ALT1: {safe stem: path} for the pictures in my_art/ ("Sol Ring.png" -> "sol_ring"; "Sol Ring__C21_263.jpg" ->
    "sol_ring__c21_263"). Names are compared case-insensitively, any punctuation (a "/" written as "_") counts as "_"."""
    out = {}
    try:
        names = os.listdir(folder) if folder else []
    except OSError:
        names = []
    for fn in sorted(names):
        stem, ext = os.path.splitext(fn)
        if ext.lower() in CUSTOM_EXTS:
            out.setdefault(custom_stem(stem), os.path.join(folder, fn))
    return out


class ArtLoader:
    """Every picture is keyed by card_data.art_key(): the card's name (a str) for Scryfall's default printing, as before Round
    ALT1, or an ArtKey(name, set, collector number) for a chosen printing. Every method takes either."""

    def __init__(self, store, prefetch_names=None, custom_dir=None):
        self.store = store
        self.imgs = {}                  # key -> Surface at SOURCE_SIZE
        self.missing = {}               # key -> time.time() after which it may be retried ('never' = inf)
        self.bad = set()
        self.error = None
        self.data_ready = threading.Event()
        self.stage = "Loading card data..."
        self._requested = set()
        self._bumped = set()
        self._jobs = queue.PriorityQueue()
        self._done = queue.Queue()
        self._seq = itertools.count()
        self._preview = {}
        self._requested_large = set()       # round 26: names with a large/art_crop picture already queued
        self.custom_dir = custom_dir        # round ALT1: my_art/ (None: no custom pictures)
        self.custom = scan_custom(custom_dir)
        self.custom_gen = 0                 # bumped by reload_custom(), part of every cache key of a custom picture
        self.custom_refused = {}            # path -> why (too big, unreadable); the table toasts each once
        self._custom_imgs = {}              # path -> full-size Surface (loaded once, on the GUI thread)
        self._printings_failed = {}
        self._from_custom = {}
        self._thread = threading.Thread(target=self._worker, daemon=True, name="art-loader")
        self._thread.start()
        names = sorted({art_key(n) for n in (prefetch_names or [])}, key=str)
        if names:
            self._jobs.put((-1, next(self._seq), "prefetch", sorted({key_name(n) for n in names})))
            for n in names:
                self._requested.add(n)
                self._jobs.put((1, next(self._seq), "art", n))
        else:
            self.data_ready.set()

    def add_names(self, names):
        """More cards to fetch in the background (a new game with different decks)."""
        names = sorted({art_key(n) for n in names} - set(self.imgs), key=str)
        if not names:
            return
        self.data_ready.clear()
        self._jobs.put((-1, next(self._seq), "prefetch", sorted({key_name(n) for n in names})))
        for n in names:
            if n not in self._requested:
                self._requested.add(n)
                self._jobs.put((1, next(self._seq), "art", n))

    # ---- worker thread ----
    def _worker(self):
        while True:
            _prio, _seq, kind, payload = self._jobs.get()
            try:
                if kind == "prefetch":
                    self.store.prefetch_cards(payload)
                    self.data_ready.set()
                    continue
                if kind == "printings":                            # round ALT1: the picker's list of printings
                    got = self.store.list_printings(payload)
                    if got is None:
                        self._printings_failed[payload] = []
                    self._done.put((kind, payload, None, None if got is not None else self.store.last_error))
                    continue
                if kind in ("large", "art_crop", "small"):         # round 26: a bigger picture, fetched in the background
                    path = self.store.get_image_path(payload, size=kind)
                    self._done.put((kind, payload, path, None if path else self.store.last_error))
                    continue
                path = self.store.get_image_path(payload)
                self._done.put(("art", payload, path, None if path else self.store.last_error))
            except Exception as e:                    # the loader thread must never die silently
                if kind == "prefetch":
                    self.data_ready.set()
                    continue
                self._done.put((kind, payload, None, f"{type(e).__name__}: {e}"))

    # ---- GUI thread ----
    def collect(self, limit=IMAGES_PER_FRAME):
        """Turn finished downloads into Surfaces. Call once per frame after the display exists. Returns how many finished jobs it
        handled (the table redraws when a picture arrived)."""
        done = 0
        for _ in range(limit):
            try:
                kind, name, path, err = self._done.get_nowait()
            except queue.Empty:
                return done
            done += 1
            if kind == "printings":
                continue
            if kind in ("large", "art_crop", "small"):  # round 26: nothing to build here -- preview()/art_crop() re-peek the file
                self._requested_large.discard((kind, name))
                if not path:
                    self.error = err
                continue
            self._requested.discard(name)
            if not name:
                self.error = err
                continue
            if name in self.imgs:
                continue
            if not path:
                self.missing[name] = time.time() + RETRY_SECONDS
                self.error = err or f"No art available for '{name}'"
                continue
            try:
                img = pygame.image.load(path).convert()
                self.imgs[name] = pygame.transform.smoothscale(img, SOURCE_SIZE)
                self.missing.pop(name, None)
            except Exception as e:
                self.bad.add(name)
                self.error = f"pygame could not load the image for '{name}': {e}"
        return done

    def _request_bigger(self, name, kind):
        """Queue a background fetch of the 'large' or 'art_crop' picture for `name`, once (round 26)."""
        key = (kind, name)
        if key in self._requested_large:
            return
        self._requested_large.add(key)
        self._jobs.put((0, next(self._seq), kind, name))

    # ---- round ALT1: my_art/ ----
    def custom_path(self, key):
        """The my_art/ picture for this key, or None: "<name>__<set>_<cn>" first (that printing), then "<name>" (every
        printing). A DFC is also looked up by its front face's name."""
        if not self.custom:
            return None
        key = art_key(key)
        name = key_name(key)
        names = [name] + ([name.split("/")[0].strip()] if "/" in name else [])
        for n in names:
            if is_art_key(key):
                p = self.custom.get(custom_stem(f"{n}__{key.set_code}_{key.collector_no}"))
                if p:
                    return p
        for n in names:
            p = self.custom.get(custom_stem(n))
            if p:
                return p
        return None

    def _custom_surface(self, path):
        """The full-size picture from my_art/, or None when it can't be used (refused once, with the reason)."""
        if path in self._custom_imgs:
            return self._custom_imgs[path]
        if path in self.custom_refused:
            return None
        try:
            img = pygame.image.load(path)
            if max(img.get_size()) > CUSTOM_MAX_SIDE:
                self.custom_refused[path] = (f"{os.path.basename(path)} is {img.get_width()}x{img.get_height()}; pictures in "
                                             f"my_art can be at most {CUSTOM_MAX_SIDE} px on a side")
                return None
            img = img.convert_alpha() if img.get_alpha() is not None else img.convert()
        except Exception as e:
            self.custom_refused[path] = f"{os.path.basename(path)} can't be read: {e}"
            return None
        self._custom_imgs[path] = img
        return img

    def is_custom(self, key):
        """True when this key's picture comes from my_art/ (and could be loaded)."""
        p = self.custom_path(key)
        return bool(p) and self._custom_surface(p) is not None

    def reload_custom(self):
        """Scan my_art/ again (the picker's "Reload my art"): forget every custom picture so the new files are used."""
        old = set(self.custom.values())
        self.custom = scan_custom(self.custom_dir)
        self._custom_imgs.clear()
        self.custom_refused.clear()
        self.custom_gen += 1
        for k in [k for k in self.imgs if getattr(self, "_from_custom", {}).get(k) in old or self.custom_path(k)]:
            self.imgs.pop(k, None)
        self._from_custom = {}
        self._preview.clear()

    def request(self, name):
        name = art_key(name)
        if name in self.imgs or name in self.bad:
            return
        retry_at = self.missing.get(name)
        if retry_at is not None and time.time() < retry_at:
            return
        if name in self._requested:
            if name not in self._bumped:
                self._bumped.add(name)
                self._jobs.put((0, next(self._seq), "art", name))
            return
        self._requested.add(name)
        self._bumped.add(name)
        self._jobs.put((0, next(self._seq), "art", name))

    def get(self, name):
        """The card's picture (a Surface) or None; asks for it in the background when missing."""
        name = art_key(name)
        if name in self.imgs:
            return self.imgs[name]
        p = self.custom_path(name)
        if p:
            img = self._custom_surface(p)
            if img is not None:
                self.imgs[name] = pygame.transform.smoothscale(img, SOURCE_SIZE)
                if not hasattr(self, "_from_custom"):
                    self._from_custom = {}
                self._from_custom[name] = p
                return self.imgs[name]
        self.request(name)
        return None

    def unavailable(self, name):
        """True when a picture is not coming soon (a token, say), so a drawn card should be used."""
        name = art_key(name)
        return name in self.bad or (name in self.missing and name not in self._requested)

    def pending(self):
        return len(self._requested)

    def preview(self, name, w, h):
        """A larger picture for the preview panel, straight from the cached file. When the panel is wider than the normal
        picture (488px), the large (672x936) picture is asked for in the background and used once it lands; the normal
        picture is used meanwhile so the preview is never empty (round 26)."""
        name = art_key(name)
        key = (name, w, h)
        if key in self._preview:
            return self._preview[key]
        custom = self.custom_path(name)
        if custom:
            img = self._custom_surface(custom)
            if img is not None:
                surf = pygame.transform.smoothscale(img, (w, h))
                self._remember_preview(key, surf)
                return surf
        path = None
        if w > 488:
            path = self.store.peek_image_path(name, size="large")
            if not path:
                self._request_bigger(name, "large")
        if not path:
            path = self.store.peek_image_path(name)
            if not path:
                self.request(name)
                return None
        try:
            surf = pygame.transform.smoothscale(pygame.image.load(path).convert(), (w, h))
        except Exception:
            return None
        self._remember_preview(key, surf)
        return surf

    def _remember_preview(self, key, surf):
        if len(self._preview) >= 12:
            self._preview.pop(next(iter(self._preview)))
        self._preview[key] = surf

    def small(self, key, w, h):
        """Round ALT1: the "small" (146x204) Scryfall picture for the printings strip, or None while it downloads."""
        key = art_key(key)
        pkey = ("small", key, w, h)
        if pkey in self._preview:
            return self._preview[pkey]
        path = self.store.peek_image_path(key, size="small")
        if not path:
            self._request_bigger(key, "small")
            return None
        try:
            surf = pygame.transform.smoothscale(pygame.image.load(path).convert(), (w, h))
        except Exception:
            return None
        if len(self._preview) >= 48:
            self._preview.pop(next(iter(self._preview)))
        self._preview[pkey] = surf
        return surf

    def printings(self, name):
        """Round ALT1: the card's printings ([{set, cn, set_name, released, ...}]) once fetched, else None (asked for in the
        background; "Loading printings..." meanwhile). [] when there are none or Scryfall can't be reached."""
        peek = getattr(self.store, "peek_printings", None)
        got = peek(name) if peek else []
        if got is not None:
            return got
        if ("printings", name) in self._requested_large:
            return self._printings_failed.get(name)
        self._requested_large.add(("printings", name))
        self._jobs.put((0, next(self._seq), "printings", name))
        return None

    def art_crop(self, name, w, h):
        """The Scryfall art_crop picture for `name` at (w, h), or None while it is still downloading (round 26: board
        frames for small battlefield cards). Asked for in the background the first time it's missing."""
        name = art_key(name)
        key = ("crop", name, w, h)
        if key in self._preview:
            return self._preview[key]
        if self.custom_path(name):                      # round ALT1: a custom picture has no art crop - the full picture is used
            return None
        path = self.store.peek_image_path(name, size="art_crop")
        if not path:
            self._request_bigger(name, "art_crop")
            return None
        try:
            surf = pygame.transform.smoothscale(pygame.image.load(path).convert(), (w, h))
        except Exception:
            return None
        self._remember_preview(key, surf)
        return surf
