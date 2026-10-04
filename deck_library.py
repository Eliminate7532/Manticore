# SPDX-License-Identifier: GPL-3.0-or-later
"""
deck_library.py - the decks you can pick from in the game's deck screen (no pygame here, so it is easy to test).

Decks live as plain text files (the same Moxfield / Archidekt text export you paste in):
  my_decks/            decks you imported; each import is saved here as <name>.txt
  my_decks/_removed/   'Remove' moves a deck here instead of deleting it, so nothing is ever lost
  sample_decks/        decks that ship with the program (listed too, but they can't be removed)

Round FMT1: a deck's format is a "# format: brawl" line in its file (formats.py); no line = Commander. DeckEntry.format.
Round ALT2: a card's MPC Autofill picture is a "# art: Sol Ring = mpc:<Google Drive id>" line (set_mpc_art); it overrides the
card line's "(SET) CN" for the pictures and leaves the card line as Moxfield wrote it.
Patch 40: a picture imported into this deck (deck_art.py) is a "# art: Sol Ring = image:<id>" line (set_imported_art); a
double-faced card's back face can have one of its own ("# art: Searstep Pathway = image:<id>").
"""
import os
import re
import shutil

import deck_art
import formats
import mpc_art
import paths
from deck_importer import DeckImportError, import_from_text, import_from_text_with_printings

# Round 28: LIBRARY_DIR (my_decks/, a player's own decks) moves to the per-user folder in an installed
# copy; SAMPLE_DIR (the decks that ship with the program) always stays in the program folder, in both
# modes - it's read-only in use either way. BASE_DIR keeps its old meaning (the program folder) for
# DeckEntry.id, which is a path relative to where the .py files live, not to where decks are now stored.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = paths.library_dir()
SAMPLE_DIR = paths.sample_dir()
REMOVED = "_removed"
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


SAMPLE_NAMES = {                                             # file name in sample_decks/ -> the name on the deck screen
    "typal_lathril": "Tap for Mana, Tap for Violence (Typal)",
    "go_wide_adeline": "Clown Car Crusade (Tokens / Go Wide)",
    "aristocrats_teysa": "Organ Harvesting for Fun & Profit (Aristocrats)",
    "voltron_light_paws": "What Does The Fox Say (Voltron)",
    "spellslinger_veyran": "This Turn Could Have Been an Email (Spellslinger)",
    "stompy_goreclaw": "We Just Need to Punch Them (Stompy)",  # patch 38: Karl's Goreclaw deck, in the alpha instead of Kinnan
    # Round FMT1: MTG Arena Brawl samples (their files start with "# format: brawl")
    "brawl_nissa": "Every Forest Is a Weapon (Lands)",
    "brawl_krenko": "Goblin Union Meeting (Goblins)",
    "brawl_elas": "Polite Murder Society (Aristocrats)",
    "brawl_tetsuko": "Nobody Saw That Coming (Unblockable)",
}


# Patch 38: the Kinnan list left the alpha (Goreclaw took its place) and is now only a test deck, tests/fixtures/decks/. Tests that
# put it in a sample folder still see its old name.
TEST_DECK_NAMES = {"kinnan_nbc_moxfield_export": "Kinnan NBC (sample)"}


def display_name(stem, builtin):
    if builtin:
        return SAMPLE_NAMES.get(stem) or TEST_DECK_NAMES.get(stem) or stem.replace("_", " ").strip().title() + " (sample)"
    return stem


def resolve_flavor_names(commanders, deck, runtime=None):
    """Some Secret Lair / Universes Beyond printings are exported under the joke/crossover name printed on that
    specific card (e.g. "Chaos Theory" for Chaos Warp) instead of the card's real rules name, which Forge's database
    does not know. Looks up anything Forge does not recognise against Scryfall's card data and swaps in the real
    name wherever that explains it (best-effort - offline or no match just leaves the name as it was).
    Returns (commanders, deck, renames) where renames is {original_name: real_name} for whatever was actually
    changed, so the caller can tell the player what happened rather than silently changing card names on them."""
    commanders, deck = list(commanders or []), list(deck or [])
    try:
        from deck_importer import resolve_flavor_names as _resolve
        mapping = _resolve(commanders + deck, runtime)
    except Exception:                                       # a card check must never stop the menu
        mapping = {}
    if not mapping:
        return commanders, deck, {}
    commanders = [mapping.get(c, c) for c in commanders]
    deck = [mapping.get(c, c) for c in deck]
    return commanders, deck, mapping


def unknown_in(entry, runtime=None):
    """Round 28d (F2): the cards of a DeckEntry that this Forge doesn't know ([] when it can't tell). Never raises."""
    try:
        import forge_client as fc
        return fc.unknown_cards(list(entry.commanders or []) + list(entry.deck or []), runtime, wait=False)     # patch 38
    except Exception:
        return []


def describe_problems(commanders, deck, error=None, runtime=None, fmt=None):
    """Things worth telling the player about a deck before starting: [(text, is_blocking)]. fmt (round FMT1): the deck's format."""
    if error:
        return [(error, True)]
    out = []
    if not commanders:
        out.append(("No commander found. Put the commander on the first line, or under a line that says Commander.", True))
    try:
        import forge_client as fc
        missing = fc.unknown_cards(list(commanders or []) + list(deck or []), runtime, wait=False)     # patch 38: never freeze
    except Exception:                                       # a card check must never stop the menu
        missing = []
    if missing:
        shown = ", ".join(missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")
        # Round 28d (F2): say it plainly - Forge leaves a card it doesn't know out of the game. The usual reason once the alpha
        # is out: a card from a set newer than the Forge build this copy is pinned to (ROADMAP Phase F).
        out.append((f"Not in this version of Forge yet (or misspelt): {shown}. They will be left out of the game.", False))
    out += legality_lines(commanders, deck, fmt)
    return out


def deck_id(path, base_dir=BASE_DIR):
    """A deck's id (remembered in settings.json): its path relative to the program folder, "my_decks/X.txt" or
    "sample_decks/X.txt" in a portable copy, exactly as before. When the deck isn't under the program folder - an installed
    copy keeps my_decks/ in the per-user folder (Round 28) - the id is "<its folder>/<file>". os.path.relpath raises
    ValueError on Windows when the two paths are on different drives (program on D:, %APPDATA% on C:), which crashed the
    deck screen; it also gave "../../Users/..." ids that change if the program moves."""
    try:
        rel = os.path.relpath(path, base_dir)
    except ValueError:                                       # different drives (Windows)
        rel = None
    if rel is None or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        rel = os.path.join(os.path.basename(os.path.dirname(os.path.abspath(path))), os.path.basename(path))
    return rel.replace("\\", "/")


def legality(commanders, deck, fmt=None):
    """Round BAN1: legality.check() for a deck, or None when the check itself failed (it must never stop the menu)."""
    try:
        import legality as lg
        return lg.check(commanders, deck, fmt=formats.normal(fmt))
    except Exception:
        return None


def legality_lines(commanders, deck, fmt=None):
    """Round BAN1: the format-legality lines (banned, not legal, 100 cards, singleton, colours, commander, pair). Every one is a
    warning: a not-legal deck still plays (Karl, 1 Oct). The 100-card line used to be here; it is legality's now."""
    fmt = formats.normal(fmt)
    v = legality(commanders, deck, fmt)
    if v is None:
        total, size = len(deck or []) + len(commanders or []), formats.get(fmt).deck_size
        return ([(f"This deck has {total} cards; a {formats.name(fmt)} deck has {size} (commander included).", False)]
                if total != size else [])
    import legality as lg
    return lg.problem_lines(v, fmt)


class DeckEntry:
    """One deck file. `commanders`/`deck` are None when the file could not be read (then `error` says why)."""

    def __init__(self, path, name, builtin, base_dir=BASE_DIR):
        self.path = path
        self.name = name
        self.builtin = builtin
        self.id = deck_id(path, base_dir)
        self.commanders = None
        self.deck = None
        self.printings = {}          # round ALT1: {card name lower: (set code lower, collector number)} from the file's lines
        self.format = formats.DEFAULT    # round FMT1: the file's "# format:" line ("commander" when it has none)
        self.error = None
        self._mtime = None

    def load(self):
        """(Re)read the file if it changed. Returns self."""
        try:
            mtime = os.path.getmtime(self.path)
        except OSError as e:
            self.commanders = self.deck = None
            self.error = f"Can't read {os.path.basename(self.path)}: {e}"
            return self
        if mtime == self._mtime and (self.deck is not None or self.error):
            return self
        self._mtime = mtime
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:      # utf-8-sig tolerates a Notepad byte-order mark
                text = f.read()
            self.format = formats.from_text(text)
            self.commanders, self.deck, self.printings = import_from_text_with_printings(text)
            names = {n.lower() for n in list(self.commanders or []) + list(self.deck or [])}
            for name, (kind, pic) in art_lines(text).items():          # round ALT2 / patch 40: "# art: <card> = mpc:|image:<id>"
                if kind == "image":                                     # patch 40: a back face's picture is kept by that face's
                    self.printings[name] = (deck_art.IMG_SET, pic)      # name, which isn't a card line of the deck
                elif name in names:
                    self.printings[name] = (mpc_art.MPC_SET, pic)
            self.error = None
        except (OSError, UnicodeDecodeError, DeckImportError) as e:
            self.commanders = self.deck = None
            self.printings = {}
            self.error = f"Can't read this deck: {e}"
        return self

    @property
    def ok(self):
        return self.deck is not None and bool(self.commanders)

    @property
    def total(self):
        return (len(self.deck) + len(self.commanders)) if self.deck is not None else 0

    def summary(self):
        """'Kinnan, Bonder Prodigy - 100 cards'"""
        if self.error:
            return self.error
        who = " + ".join(self.commanders) if self.commanders else "no commander found"
        return f"{who}  -  {self.total} cards"

    def problems(self, runtime=None):
        """describe_problems() for this deck; remembered until the file changes (or, Round BAN1, the banned list or card data
        changes: legality.version()), because the deck screen asks every frame."""
        key = (self._mtime, runtime, _legality_version(), self.format, _card_index_ready(runtime))
        if getattr(self, "_problems_key", None) != key:
            self._problems = describe_problems(self.commanders, self.deck, self.error, runtime, self.format)
            self._problems_key = key
        return self._problems


    def legality(self, runtime=None):
        """Round BAN1: the deck's legality.Verdict (None when the file can't be read or the check failed). Same cache rule."""
        if self.error or self.deck is None:
            return None
        key = (self._mtime, _legality_version(), self.format)
        if getattr(self, "_legality_key", None) != key:
            self._legality = legality(self.commanders, self.deck, self.format)
            self._legality_key = key
        return self._legality


def _card_index_ready(runtime):
    """Patch 38: whether Forge's card-name index is ready, so the deck's problems are worked out again once it is (the
    "not in this version of Forge" line needs it)."""
    try:
        import forge_client as fc
        return fc.card_index_ready(runtime)
    except Exception:
        return True


def _legality_version():
    try:
        import legality as lg
        return lg.version()
    except Exception:
        return 0


def safe_file_name(name):
    name = " ".join(_BAD_NAME_CHARS.sub(" ", (name or "")).split()).strip(" .")
    return name[:80] or "Deck"


def _entries(folder, builtin, base_dir):
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder), key=str.lower):
        path = os.path.join(folder, fn)
        if fn.lower().endswith(".txt") and os.path.isfile(path):
            out.append(DeckEntry(path, display_name(os.path.splitext(fn)[0], builtin), builtin, base_dir).load())
    return out


def list_decks(library_dir=LIBRARY_DIR, sample_dir=SAMPLE_DIR, base_dir=BASE_DIR):
    """Your imported decks first (A-Z), then the bundled samples."""
    return _entries(library_dir, False, base_dir) + _entries(sample_dir, True, base_dir)


def find(entries, deck_id):
    return next((e for e in entries if e.id == deck_id), None)


def looks_like_link(text):
    t = (text or "").strip()
    return bool(re.match(r"^https?://\S+$", t)) or (t.count("\n") == 0 and ("moxfield.com" in t or "archidekt.com" in t))


def analyse(text):
    """Read pasted text. Returns (commanders, deck, error). The error is written for the player."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None, None, "Nothing pasted yet."
    if looks_like_link(text):
        return None, None, ("That is a web link, and this box needs the deck's TEXT list instead. On Moxfield open the deck, "
                            "choose Export, and copy the text; on Archidekt use Export > Text.")
    try:
        commanders, deck = import_from_text(text)
    except DeckImportError as e:
        return None, None, str(e)
    if not commanders:
        n = len(deck)
        return None, None, (f"I read {n} card{'s' if n != 1 else ''} but could not tell which one is the commander. "
                            "Put the commander on the first line of a 100-card list, or add a line that says Commander above it.")
    return commanders, deck, None


def suggest_name(commanders):
    return safe_file_name(commanders[0] if commanders else "New deck")


def free_name(name, library_dir=LIBRARY_DIR):
    """`name`, or `name (2)`, `name (3)` ... when that file already exists."""
    name = safe_file_name(name)
    candidate, n = name, 2
    existing = {f.lower() for f in os.listdir(library_dir)} if os.path.isdir(library_dir) else set()
    while candidate.lower() + ".txt" in existing:
        candidate = f"{name} ({n})"
        n += 1
    return candidate


def save_text(name, text, library_dir=LIBRARY_DIR, base_dir=BASE_DIR, fmt=None):
    """Check the pasted text and save it as a new deck. Returns the DeckEntry; raises DeckImportError with a readable message.
    fmt (round FMT1): the deck's format, written as a "# format:" line (None keeps the text's own line, if it has one)."""
    commanders, deck, error = analyse(text)
    if error:
        raise DeckImportError(error)
    os.makedirs(library_dir, exist_ok=True)
    name = free_name(name or suggest_name(commanders), library_dir)
    path = os.path.join(library_dir, name + ".txt")
    tmp = path + ".tmp"
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if fmt is not None:
        text = formats.with_format(text, fmt)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.strip() + "\n")
        os.replace(tmp, path)
    except OSError as e:
        raise DeckImportError(f"Could not save the deck: {e}")
    return DeckEntry(path, name, False, base_dir).load()


def remove(entry, library_dir=LIBRARY_DIR):
    """Take a deck out of the list by moving its file into my_decks/_removed/ (never a real delete). Returns the new path."""
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be removed.")
    folder = os.path.join(library_dir, REMOVED)
    os.makedirs(folder, exist_ok=True)
    stem, n = os.path.splitext(os.path.basename(entry.path))[0], 1
    target = os.path.join(folder, stem + ".txt")
    while os.path.exists(target):
        n += 1
        target = os.path.join(folder, f"{stem} ({n}).txt")
    shutil.move(entry.path, target)
    return target


# ---- Round ALT1: a chosen printing, written into the deck file ---------------------------------------------------------------------

_LINE = re.compile(r"^(?P<lead>\s*(?:\d+x?\s+)?)(?P<rest>.+?)\s*$")


def _with_printing(rest, printing):
    """`rest` (a deck line after its quantity) with its "(SET) CN" replaced by `printing`, added, or (printing None) removed.
    Foil / etched markers and [tags] are kept, in their place at the end."""
    from deck_importer import _PRINTING
    tail = ""
    while True:                                     # peel tags and foil markers off the end, to put them back afterwards
        m = re.search(r"\s*(\[[^\]]*\]|\*[A-Za-z]+\*)\s*$", rest)
        if not m:
            break
        tail = " " + m.group(1) + tail
        rest = rest[:m.start()]
    rest = _PRINTING.sub("", rest).rstrip()
    if printing:
        rest += f" ({printing[0].upper()}) {printing[1]}"
    return rest + tail


def set_printing(entry, card_name, printing):
    """Rewrite every line of `entry`'s file that is `card_name` so it names `printing` ((set, cn), or None for the default).
    Only the "(SET) CN" part changes; quantity, foil marker and tags stay. Atomic (.tmp, then os.replace). Returns how many lines
    changed. A sample deck is refused (the deck screen offers to copy it first). Round ALT2: the card's MPC Autofill line, if
    it has one, goes too (a Scryfall printing or the default replaces it); `printing` may itself be ("_mpc", id), which is
    set_mpc_art(). Patch 40: the same for an imported picture's line, and ("_img", id) sets one."""
    from deck_importer import _strip_printing_info, _SECTION_HEADER
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be changed; save a copy to My decks first.")
    if mpc_art.is_mpc_printing(printing):
        return set_mpc_art(entry, card_name, printing[1])
    if deck_art.is_img_printing(printing):
        return set_imported_art(entry, {card_name: printing[1]}) > 0
    want = card_name.strip().lower()
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out, changed = [], 0
    for line in text.splitlines():
        a = _ART_LINE.match(line)
        if a and a.group("name").strip().lower() == want:      # round ALT2 / patch 40: its MPC or imported picture goes too
            changed += 1
            continue
        m = _LINE.match(line)
        if m and line.strip() and not _SECTION_HEADER.match(line.strip()) and _strip_printing_info(m.group("rest")).lower() == want:
            new = m.group("lead") + _with_printing(m.group("rest"), printing)
            if new != line:
                changed += 1
            line = new
        out.append(line)
    if changed:
        tmp = entry.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, entry.path)
        except OSError as e:
            raise DeckImportError(f"Could not save the deck: {e}")
        entry._mtime = None                         # re-read even if the clock didn't move
        entry.load()
    return changed


# ---- Round ALT2: a picture from MPC Autofill, as a comment line (patch 40: or an imported one) ------------------------------------

_ART_LINE = re.compile(r"^\s*#\s*art\s*:\s*(?P<name>.+?)\s*=\s*(?P<kind>mpc|image)\s*:\s*(?P<id>\S+)\s*$", re.IGNORECASE)


def art_lines(text):
    """{card name lower: (kind, id)} from a deck file's "# art:" lines, kind "mpc" (a Google Drive id) or "image" (an imported
    picture, deck_art.py). The last line for a card wins; a line whose id can't be one is ignored."""
    out = {}
    for line in (text or "").splitlines():
        m = _ART_LINE.match(line)
        if not m:
            continue
        kind, pic = m.group("kind").lower(), m.group("id")
        if (mpc_art.valid_id(pic) if kind == "mpc" else deck_art.valid_id(pic)):
            out[m.group("name").strip().lower()] = (kind, pic)
    return out


def mpc_art_lines(text):
    """{card name lower: Drive id} from a deck file's "# art: <card> = mpc:<id>" lines (the last line for a card wins; a line
    with an id that can't be a Drive id is ignored)."""
    return {name: pic for name, (kind, pic) in art_lines(text).items() if kind == "mpc"}


def art_line(card_name, drive_id, kind="mpc"):
    return f"# art: {card_name} = {kind}:{drive_id}"


def _write_lines(entry, out):
    tmp = entry.path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, entry.path)
    except OSError as e:
        raise DeckImportError(f"Could not save the deck: {e}")
    entry._mtime = None
    entry.load()


def _art_insert_at(lines):
    at = 0
    while at < len(lines) and lines[at].lstrip().startswith("#"):
        at += 1
    return at


def set_imported_art(entry, pictures):
    """Patch 40: use imported pictures in this deck - {card or face name: picture id} (deck_art.py). Each name's earlier "# art:"
    line (MPC or imported) is replaced; the new lines go together at the top, after any other leading "#" lines, sorted by
    name. One atomic write. Returns how many lines were written. A sample deck is refused."""
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be changed; save a copy to My decks first.")
    bad = [p for p in pictures.values() if not deck_art.valid_id(p)]
    if bad:
        raise DeckImportError(f"'{bad[0]}' isn't an imported picture.")
    if not pictures:
        return 0
    want = {n.strip().lower() for n in pictures}
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    out = [ln for ln in lines if not ((a := _ART_LINE.match(ln)) and a.group("name").strip().lower() in want)]
    at = _art_insert_at(out)
    new = [art_line(n.strip(), p, "image") for n, p in sorted(pictures.items(), key=lambda kv: kv[0].lower())]
    out[at:at] = new
    if out != lines:
        _write_lines(entry, out)
    return len(new)


def imported_art_count(entry):
    """Patch 40: how many "# art: ... = image:" lines the deck file has."""
    try:
        with open(entry.path, "r", encoding="utf-8-sig") as f:
            return sum(1 for kind, _p in art_lines(f.read()).values() if kind == "image")
    except OSError:
        return 0


def remove_imported_art(entry):
    """Patch 40: take every imported picture out of this deck (its "# art: ... = image:" lines; MPC lines stay). Returns how
    many lines went. The pictures stay in deck_art/ (another deck may use them)."""
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be changed; save a copy to My decks first.")
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    out = [ln for ln in lines if not ((a := _ART_LINE.match(ln)) and a.group("kind").lower() == "image")]
    if out != lines:
        _write_lines(entry, out)
    return len(lines) - len(out)


def read_text(entry):
    """The deck file's text as it is now (patch 40: kept so an import can be undone)."""
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        return f.read()


def restore_text(entry, text):
    """Patch 40: put the deck file back to `text` (Undo an import). Atomic."""
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be changed.")
    _write_lines(entry, text.splitlines())


def set_mpc_art(entry, card_name, drive_id):
    """Use an MPC Autofill picture for `card_name` in this deck (drive_id None: remove it, back to the card line's printing).
    Writes one "# art:" line at the top of the file, after any other leading "#" lines ("# format: brawl"); the card lines are
    not touched, so the deck still pastes into Moxfield. Atomic. Returns True when the file changed. A sample deck is refused.
    Patch 40: an imported picture's line for the card goes too."""
    if entry.builtin:
        raise DeckImportError("The sample decks that come with the program can't be changed; save a copy to My decks first.")
    if drive_id is not None and not mpc_art.valid_id(drive_id):
        raise DeckImportError(f"'{drive_id}' isn't an MPC Autofill picture.")
    want = card_name.strip().lower()
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    lines = text.splitlines()
    out = [ln for ln in lines if not ((a := _ART_LINE.match(ln)) and a.group("name").strip().lower() == want)]
    if drive_id is not None:
        out.insert(_art_insert_at(out), art_line(card_name.strip(), drive_id))
    if out == lines:
        return False
    _write_lines(entry, out)
    return True


def copy_to_library(entry, library_dir=LIBRARY_DIR, base_dir=BASE_DIR):
    """A sample deck saved as a new deck in My decks (same text, a free name). Returns the new DeckEntry."""
    with open(entry.path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    return save_text(free_name(entry.name, library_dir), text, library_dir, base_dir)
