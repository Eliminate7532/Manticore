# SPDX-License-Identifier: GPL-3.0-or-later
"""
deck_art.py - Patch 40: card pictures imported into ONE deck from a .zip or a folder (Proxxied's "ZIP Archive" export, say).

my_art/ (round ALT1) gives a card the same picture in every deck. This is per deck: the Card art window's "Import pictures"
(or a .zip dragged onto that window) reads the pictures, matches each one to a card of the deck that window is showing by its
file name, and writes a "# art: <card> = image:<id>" line into that deck file only (deck_library.set_imported_art). Other
decks are not touched, and the deck's card lines stay as Moxfield wrote them.

The pictures themselves are kept in deck_art/ beside my_art/ (paths.deck_art_dir), one JPEG per picture, named by the first 16
hex digits of the SHA-256 of the file it came from: the same picture imported twice (or into two decks) is stored once.
Each is stored as the card alone, 1400 pixels high at most: a print file's bleed is cut off (the same shape test as MPC
Autofill pictures, mpc_art.bleed_box), and a bigger picture is scaled down. Nothing is sent anywhere.

File names: "001 - Sol Ring.png" (Proxxied), "Sol Ring.jpg", "Sol Ring (C21) 263.png", "Sol Ring_front.png". Compared without
case, accents or punctuation, so "Chandra's Incinerator", "Big Apple, 3 a.m." and "Spiked Corridor __ Torture Pit" (a file name
can't hold "//") all match. A double-faced card's back ("109 - Searstep Pathway.png", the back of Blightstep Pathway) is matched
through Scryfall's card data and kept under the back face's name, so the table shows it when that face is up. A picture with
no card of this deck is listed, never guessed: "101 - Default.png" (Proxxied's card back) is counted as a card back.

A choice is a card_data ArtKey whose set code is IMG_SET ("_img") and whose "collector number" is the picture's id.
"""
import hashlib
import io
import os
import re
import struct
import threading
import unicodedata
import zipfile

import paths

IMG_SET = "_img"
KEPT_H = 1400                          # stored pictures are at most this many pixels high (the same as a kept MPC picture)
PICTURE_EXTS = (".png", ".jpg", ".jpeg")
MAX_FILE_BYTES = 200 * 1024 * 1024     # a single picture bigger than this is skipped (Proxxied's biggest at 1200 DPI: ~20 MB)
MAX_SIDE = 16000                       # ... and one wider or taller than this, or of more than MAX_PIXELS, before decoding it
MAX_PIXELS = 120_000_000
BACK_NAMES = {"default", "back", "card_back", "cardback", "backside", "back_side"}

_ID = re.compile(r"[0-9a-f]{16}")
_LEAD_NUMBER = re.compile(r"^\s*\d{1,4}\s*(?:[-_.)]\s*|\s+)(?=\S)")      # "001 - ", "001_", "1. ", "001 "
_COPY_SUFFIX = re.compile(r"\s*\(\d{1,3}\)\s*$")                        # "Sol Ring (1)": a second download of the same file
_SIDE_SUFFIX = re.compile(r"[\s_-]+(front|back)\s*$", re.IGNORECASE)
_PRINTING_SUFFIX = re.compile(r"\s*[\(\[][A-Za-z0-9]{2,6}[\)\]](?:\s*[-_]?\s*[A-Za-z0-9★*-]{1,8})?\s*$")   # " (C21) 263", " [MH3]"
_FACE_SPLIT = re.compile(r"\s*(?://|__|\s_\s)\s*")                       # "A // B" in a deck, "A __ B" in a file name
_DECK_FACE_SPLIT = re.compile(r"\s*//?\s*")                              # "Spiked Corridor/Torture Pit"


def valid_id(pic_id):
    return isinstance(pic_id, str) and bool(_ID.fullmatch(pic_id))


def is_img(key):
    """True for a picture key that names an imported picture (an ArtKey with set code IMG_SET)."""
    return isinstance(key, tuple) and getattr(key, "set_code", None) == IMG_SET and valid_id(getattr(key, "collector_no", None))


def is_img_printing(printing):
    """True for a deck's (set, number) printing that is an imported picture."""
    return bool(printing) and len(printing) == 2 and printing[0] == IMG_SET and valid_id(printing[1])


def picture_path(pic_id, folder=None):
    return os.path.join(folder or paths.deck_art_dir(), f"{pic_id}.jpg")


def picture_id(data):
    return hashlib.sha256(data).hexdigest()[:16]


# ---- names ---------------------------------------------------------------------------------------------------------------------

def norm(text):
    """How names are compared: no accents, no case, any run of punctuation or spaces as one "_"."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("æ", "ae").replace("œ", "oe").replace("’", "'")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def file_names(member):
    """(names to try, side) for one picture's file name: the name with and without a leading number, each also as its front
    face. side is "front", "back" or None, from a "_front" / "_back" ending."""
    stem = os.path.splitext(os.path.basename(member.replace("\\", "/")))[0].strip()
    stem = _COPY_SUFFIX.sub("", stem)
    side = None
    stems = [stem]
    m = _SIDE_SUFFIX.search(stem)
    if m and m.start() > 0:
        side = m.group(1).lower()
        stems.insert(0, stem[:m.start()].strip())     # "Sol Ring_front"; the whole stem is still tried ("Strike Back")
    out = []
    for s in [v for st in stems for v in (_LEAD_NUMBER.sub("", st, count=1), st)]:
        for t in (s, _PRINTING_SUFFIX.sub("", s)):
            t = t.strip()
            if t and t not in out:
                out.append(t)
            front = _FACE_SPLIT.split(t)[0].strip() if t else ""
            if front and front not in out:
                out.append(front)
    return out, side


def deck_index(card_names):
    """{norm name: deck card name}: each card by its full name, then (where free) by its front face and by each face."""
    index = {}
    for name in card_names:
        index.setdefault(norm(name), name)
    for name in card_names:
        faces = [f for f in _DECK_FACE_SPLIT.split(name) if f.strip()]
        if len(faces) > 1:
            index.setdefault(norm(" // ".join(faces)), name)
            index.setdefault(norm(faces[0]), name)
    return index


def scryfall_faces(card):
    """The face names of a card whose faces have pictures of their own (a double-faced card), from Scryfall's card data; []
    for a card with one picture (split cards, adventures and rooms show both halves on one picture)."""
    faces = (card or {}).get("card_faces") or []
    if len(faces) > 1 and all(f.get("image_uris") for f in faces):
        return [f.get("name") or "" for f in faces]
    return []


class Plan:
    """Which picture goes with which card. cards and backs are {name: member}; the lists are file names (members)."""

    def __init__(self):
        self.cards = {}          # deck card name -> member
        self.backs = {}          # a double-faced card's back face name -> member
        self.extra = []          # (member, card name): another picture of a card that already has one (only the first is used)
        self.card_backs = []     # members that are card backs ("Default")
        self.unmatched = []      # members with no card of this deck
        self.missing = []        # deck card names with no picture
        self.skipped = []        # (member, why): too big, not a picture

    @property
    def count(self):
        return len(self.cards) + len(self.backs)


def plan_import(members, card_names, faces_of=None):
    """Match picture files to a deck's cards. members: the file names; card_names: the deck's card names (commanders too);
    faces_of(card name) -> [face names] (or None) for double-faced cards, asked only when a file is left over."""
    plan = Plan()
    index = deck_index(card_names)
    left = []
    for member in members:
        names, side = file_names(member)
        hit = next((index[norm(n)] for n in names if norm(n) in index), None)
        if hit is None:
            left.append((member, names, side))
        elif hit in plan.cards:
            plan.extra.append((member, hit))
        else:
            plan.cards[hit] = member
    if left and faces_of is not None:
        backs = {}
        for name in card_names:
            try:
                faces = faces_of(name) or []
            except Exception:
                faces = []
            for face in faces[1:]:
                if face and norm(face) not in index:
                    backs.setdefault(norm(face), face)
        still = []
        for member, names, side in left:
            face = next((backs[norm(n)] for n in names if norm(n) in backs), None)
            if face is None:
                still.append((member, names, side))
            elif face in plan.backs:
                plan.extra.append((member, face))
            else:
                plan.backs[face] = member
        left = still
    for member, names, side in left:
        if side == "back" or any(norm(n) in BACK_NAMES for n in names):
            plan.card_backs.append(member)
        else:
            plan.unmatched.append(member)
    plan.missing = [n for n in dict.fromkeys(card_names) if n not in plan.cards]
    return plan


# ---- where the pictures come from -----------------------------------------------------------------------------------------------

class ZipSource:
    def __init__(self, path):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        self.skipped = []
        self._infos = {}
        for info in self.zip.infolist():
            name = info.filename
            base = os.path.basename(name.rstrip("/"))
            if info.is_dir() or name.startswith("__MACOSX/") or base.startswith("."):
                continue
            if os.path.splitext(base)[1].lower() not in PICTURE_EXTS:
                continue
            if info.file_size > MAX_FILE_BYTES:
                self.skipped.append((name, f"{info.file_size // (1024 * 1024)} MB is too big"))
                continue
            self._infos[name] = info

    def members(self):
        return list(self._infos)

    def read(self, member):
        with self.zip.open(self._infos[member]) as f:
            return f.read(MAX_FILE_BYTES + 1)

    def close(self):
        self.zip.close()


class FolderSource:
    """A folder of pictures (an unzipped export): its pictures and those one folder down."""

    def __init__(self, path):
        self.path = path
        self.skipped = []
        self._files = {}
        for root, dirs, files in os.walk(path):
            if os.path.relpath(root, path) != ".":
                dirs[:] = []                             # one folder down at most
            dirs.sort()
            for fn in sorted(files):
                if fn.startswith(".") or os.path.splitext(fn)[1].lower() not in PICTURE_EXTS:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size > MAX_FILE_BYTES:
                    self.skipped.append((rel, f"{size // (1024 * 1024)} MB is too big"))
                    continue
                self._files[rel] = full

    def members(self):
        return list(self._files)

    def read(self, member):
        with open(self._files[member], "rb") as f:
            return f.read(MAX_FILE_BYTES + 1)

    def close(self):
        pass


class FileSource(FolderSource):
    """One picture file."""

    def __init__(self, path):
        self.path = path
        self.skipped = []
        self._files = {os.path.basename(path): path}


def open_source(path):
    """A ZipSource, FolderSource or FileSource for a dropped or chosen path. Raises ValueError (a sentence) when it is none."""
    if os.path.isdir(path):
        return FolderSource(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        try:
            return ZipSource(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise ValueError(f"{os.path.basename(path)} can't be opened as a .zip: {e}")
    if ext in PICTURE_EXTS:
        if not os.path.isfile(path):
            raise ValueError(f"{os.path.basename(path)} isn't there any more.")
        return FileSource(path)
    raise ValueError(f"{os.path.basename(path)} isn't a .zip, a folder or a picture (.png, .jpg).")


# ---- turning one file into a stored picture --------------------------------------------------------------------------------------

def image_size(data):
    """(width, height) from a PNG's or JPEG's header, or None. Read before decoding, so a huge picture is refused unread."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                i += 2 if marker != 0xFF else 1
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def store_picture(data, member, folder=None):
    """Store one picture (bytes from `member`) as the card alone, at most KEPT_H high; returns its id. Raises ValueError with
    a short reason when it can't be used. A picture already stored (same file) is not decoded again."""
    import pygame                                      # only here: the rest of this module is pure Python
    import mpc_art
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("too big")
    pic_id = picture_id(data)
    dest = picture_path(pic_id, folder)
    if os.path.isfile(dest):
        return pic_id
    size = image_size(data)
    if size is None:
        raise ValueError("not a PNG or JPEG picture")
    w, h = size
    if w <= 0 or h <= 0 or max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:
        raise ValueError(f"{w}x{h} is too big")
    try:
        surf = pygame.image.load(io.BytesIO(data), os.path.basename(member))
    except Exception as e:
        raise ValueError(f"can't be read ({e})")
    box = mpc_art.bleed_box(*surf.get_size())
    if box:
        surf = surf.subsurface(pygame.Rect(box)).copy()
    if surf.get_bitsize() not in (24, 32):             # smoothscale needs 24 or 32 bits (a palette PNG isn't)
        full = pygame.Surface(surf.get_size(), 0, 32)
        full.blit(surf, (0, 0))
        surf = full
    if surf.get_height() > KEPT_H:
        surf = pygame.transform.smoothscale(surf, (max(1, round(surf.get_width() * KEPT_H / surf.get_height())), KEPT_H))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest[:-4] + f".{threading.get_ident()}.tmp.jpg"       # pygame picks the format from the extension
    try:
        pygame.image.save(surf, tmp)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return pic_id


class ImportJob:
    """Matches and stores the pictures of one source on its own thread. Read .progress (done, total) and .finished; .cancel()
    stops it between two pictures. When finished: .plan, .pictures {card or face name: id}, .failed [(member, why)], or .error
    (a sentence) when nothing could be read at all."""

    def __init__(self, path, card_names, faces_of=None, folder=None, only_card=None, start=True):
        self.path, self.card_names, self.faces_of, self.folder = path, list(card_names), faces_of, folder
        self.only_card = only_card                      # a single picture dropped on one card's page: that card, whatever its name
        self.progress = (0, 0)
        self.stage = "Reading"
        self.finished = False
        self.cancelled = False
        self.plan = None
        self.pictures = {}
        self.failed = []
        self.error = None
        self._thread = threading.Thread(target=self._run, name="deck-art-import", daemon=True)
        if start:
            self._thread.start()

    def cancel(self):
        self.cancelled = True

    def run_now(self):
        """Run on the calling thread (tests)."""
        self._run()
        return self

    def _run(self):
        source = None
        try:
            source = open_source(self.path)
            members = source.members()
            if self.only_card is not None and isinstance(source, FileSource):
                plan = Plan()
                plan.cards[self.only_card] = members[0]
            else:
                self.stage = "Matching"
                plan = plan_import(members, self.card_names, self.faces_of)
            plan.skipped = list(source.skipped)
            self.plan = plan
            if not members:
                self.error = "There are no pictures (.png or .jpg) in it."
                return
            todo = [(name, member) for name, member in list(plan.cards.items()) + list(plan.backs.items())]
            self.stage = "Importing"
            self.progress = (0, len(todo))
            for i, (name, member) in enumerate(todo):
                if self.cancelled:
                    return
                try:
                    self.pictures[name] = store_picture(source.read(member), member, self.folder)
                except Exception as e:
                    why = str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"
                    self.failed.append((member, why))
                self.progress = (i + 1, len(todo))
        except ValueError as e:
            self.error = str(e)
        except Exception as e:                           # the job must always finish, and say why it didn't work
            self.error = f"The import stopped: {type(e).__name__}: {e}"
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            self.finished = True


def display_name(member):
    """A member's file name without its folder, for messages."""
    return os.path.basename(member.replace("\\", "/"))


def summary(job, deck_name):
    """The lines the Card art window shows when an import has finished."""
    plan = job.plan
    if job.error:
        return [job.error]
    lines = []
    n_cards = sum(1 for n in job.pictures if plan and n in plan.cards)
    n_backs = len(job.pictures) - n_cards
    if not job.pictures:
        lines.append(f"No picture was matched to a card of '{deck_name}', so the deck is as it was."
                     + (" A file's name has to be the card's name (\"001 - Sol Ring.png\" or \"Sol Ring.jpg\")."
                        if plan and plan.unmatched else ""))
    elif job.only_card is not None:
        lines.append(f"{job.only_card} now uses your picture in '{deck_name}'. Other decks are not changed.")
    else:
        head = f"{n_cards} card{'s' if n_cards != 1 else ''} in '{deck_name}' now use{'s' if n_cards == 1 else ''} your pictures"
        if n_backs:
            head += f", and {n_backs} back face{'s' if n_backs != 1 else ''} of double-faced cards"
        lines.append(head + ". Other decks are not changed.")
    if plan and plan.unmatched:
        names = [os.path.splitext(display_name(m))[0] for m in plan.unmatched]
        lines.append(f"Not a card of this deck ({len(names)}): " + "; ".join(names))
    if plan and plan.missing and job.only_card is None:
        lines.append(f"No picture for ({len(plan.missing)}): " + "; ".join(plan.missing))
    if job.failed:
        lines.append(f"Couldn't be used ({len(job.failed)}): " + "; ".join(f"{display_name(m)} ({why})" for m, why in job.failed))
    if plan and plan.skipped:
        lines.append("Skipped: " + "; ".join(f"{display_name(m)} ({why})" for m, why in plan.skipped))
    if plan and plan.extra:
        lines.append(f"{len(plan.extra)} more picture{'s' if len(plan.extra) != 1 else ''} of a card that already had one "
                     "(one picture per card name; the first was used).")
    if plan and plan.card_backs:
        lines.append(f"Card back{'s' if len(plan.card_backs) != 1 else ''} left out: "
                     + "; ".join(os.path.splitext(display_name(m))[0] for m in plan.card_backs))
    return lines
