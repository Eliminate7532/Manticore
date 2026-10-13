# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_pod.py - one cube draft as you play it, kept on disk (Round CD2). No pygame.

Karl, 9 Oct 2026 23:05: "build a main menu option for draft", and the scope he chose: "Choose cube -> pick 2 at a time vs 3 AIs
-> review -> your deck Auto-built -> all 4 decks saved to My decks and chosen on the deck screen -> Start." draft_screens.py
is the window; this is what it works on, so it can be tested without one.

A pod is a folder, saves/drafts/<id>/ (paths.saves_dir(); MANTICORE_DATA_DIR wins, so a test run never touches yours):
  cards.json.gz  the cube's cards Manticore can use, in the order the draft numbers them (draft_core: a card is its index).
                 Kept with the pod, so a later change to the cube (or a refreshed copy of it) can't scramble an old draft.
                 Written once. Compressed: about 0.5 MB of card text becomes about 0.1 MB (measured 9 Oct on Commander Cube).
  draft.json     the draft (draft_core.Draft.to_dict: its seed and every pick, in order), the cube's id / name / owner, the
                 Forge-flagged names (cube_source.check_forge), the cards left out, the stage, when it started, and - once
                 saved - the four decks' ids. Written after every pick (a draft resumes after Manticore closes - the spec's
                 default, from Arena: "Arena remembers where you were in the draft").
Seat 0 is you ("You"); seats 1-3 are the AI drafters ("AI 1" .. "AI 3"), who pick at once whenever a pack is in front of them
(draft_ai.chooser; draft_core.run_ai). 903.13c: packs 1 and 3 pass left (to the next seat: AI 1), pack 2 right (to AI 3).

The decks: draft_deck.build for every seat - the AIs avoiding the cards Forge's own developers mark AI:RemoveDeck:All, yours not
(those flags are about Forge's AI piloting them, not about you). Each is saved to My decks as a Commander Draft deck
("# format: commander_draft", formats.py) with a "# draft: <pod id>" line naming this pod (the deck builder, CD4, will need the
pool back).

Round CD3: a pod has a style - "commander" (above) or "classic" (Karl, 12 Oct 2026: the classic stock cubes; 3 packs of 15, one card
a pick - draft_core.SHAPES). A classic pod's decks are draft_deck.build_classic's 40-card decks with the cube's own basic lands
(the pod keeps the cube's basics board), saved as Draft decks ("# format: draft"); a cube with a house rule (Turbo Cube's emblem)
writes "# rules: turbo" on each (house_rules.py), so every game with them plays with the rule. A pod saved before CD3 has no style
and is a Commander Draft, as it was.
"""
import datetime
import gzip
import json
import os
import random
import re
import time

import cube_source as cs
import draft_ai as da
import draft_core as dc
import draft_deck as dd
import formats
import house_rules
import paths

POD_VERSION = 1
SEAT_NAMES = ("You", "AI 1", "AI 2", "AI 3")
STATE_FILE = "draft.json"
CARDS_FILE = "cards.json.gz"
STAGES = ("drafting", "built", "saved", "discarded")     # "drafting" until the decks are saved; "discarded" = Discard was pressed
FORMAT = "commander_draft"
CLASSIC_FORMAT = "draft"                                 # round CD3: the classic draft's decks (formats.py)
_DRAFT_LINE = re.compile(r"^\s*#\s*draft\s*[:=]\s*([A-Za-z0-9_.-]+)\s*$", re.I | re.M)


class PodError(Exception):
    """A pod that can't be read (or made): the message is written for the player."""


def pods_dir():
    return os.path.join(paths.saves_dir(), "drafts")


def _safe_id(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text or "")[:40].strip("_") or "cube"


def _write_json(path, data, gz=False):
    tmp = path + ".tmp"
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if gz:
        raw = gzip.compress(raw, mtime=0)
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, path)


def _read_json(path, gz=False):
    with open(path, "rb") as f:
        raw = f.read()
    return json.loads(gzip.decompress(raw) if gz else raw)


def draft_id_of(text):
    """The pod id a deck file's "# draft: <id>" line names, or None."""
    m = _DRAFT_LINE.search(text or "")
    return m.group(1) if m else None


class Pod:
    """One draft: its cards, its Draft, and where it is kept. Make one with Pod.new or Pod.load."""

    def __init__(self, folder, cube_meta, cards, flagged, missing, draft, created, stage="drafting", deck_ids=None,
                 style="commander", basics=(), rules=()):
        self.folder = folder
        self.id = os.path.basename(folder)
        self.cube = dict(cube_meta)                  # {"id", "name", "owner"}
        self.style = style if style in cs.STYLES else "commander"      # round CD3
        self.basics = tuple(basics or ())            # round CD3: the cube's basics board (names)
        self.rules = house_rules.normal(rules)       # round CD3: the cube's house rules (Turbo Cube's emblem)
        self.cards = list(cards)
        self.flagged = frozenset(flagged or ())
        self.missing = tuple(missing or ())
        self.draft = draft
        self.created = created
        self.stage = stage
        self.deck_ids = list(deck_ids or [])
        self._z = None
        self._chooser = None

    # ---- making and keeping ---------------------------------------------------------------------------------------------
    @classmethod
    def new(cls, cube, check, seed=None, root=None, now=None, style=None):
        """A new draft of this cube (cube_source.Cube) with these playable cards (cube_source.ForgeCheck). The AIs make their
        first picks at once. Raises draft_core.DraftError when the cube is too small (its message says so in numbers).
        Round CD3: style "commander" or "classic" (default: the cube's own - a stock cube's, or guess_style's)."""
        cards = list(check.playable)
        style = style or getattr(cube, "style", None) or "commander"
        if style not in cs.STYLES:
            raise dc.DraftError(f"There is no draft style called {style!r}.")
        seed = seed if seed is not None else random.SystemRandom().randrange(1, 2 ** 31)
        seats = [dc.Seat(n, "human" if i == 0 else "ai") for i, n in enumerate(SEAT_NAMES)]
        packs, pack_size, picks = dc.shape(style)
        draft = dc.Draft(len(cards), seats, seed, packs, pack_size, picks)
        now = time.time() if now is None else now
        root = root or pods_dir()
        base = time.strftime("%Y%m%d_%H%M%S", time.localtime(now)) + "_" + _safe_id(cube.id)
        folder, n = os.path.join(root, base), 2
        while os.path.exists(folder):
            folder, n = os.path.join(root, f"{base}_{n}"), n + 1
        os.makedirs(folder)
        pod = cls(folder, {"id": cube.id, "name": cube.name or cube.id, "owner": cube.owner or ""}, cards, check.ai_flagged,
                  check.missing, draft, now, style=style, basics=getattr(cube, "basics", ()), rules=getattr(cube, "rules", ()))
        _write_json(os.path.join(folder, CARDS_FILE), {"v": POD_VERSION, "fields": list(cs.CubeCard._fields),
                                                       "cards": [list(c) for c in cards]}, gz=True)
        pod.run_ai()
        pod.save()
        return pod

    def save(self):
        _write_json(os.path.join(self.folder, STATE_FILE),
                    {"v": POD_VERSION, "cube": self.cube, "flagged": sorted(self.flagged), "missing": list(self.missing),
                     "created": self.created, "stage": self.stage, "deck_ids": self.deck_ids,
                     "style": self.style, "basics": list(self.basics), "rules": list(self.rules),      # round CD3
                     "draft": self.draft.to_dict()})

    @classmethod
    def load(cls, folder):
        """The pod kept in `folder`. Raises PodError (in words) when it can't be read."""
        try:
            state = _read_json(os.path.join(folder, STATE_FILE))
            raw = _read_json(os.path.join(folder, CARDS_FILE), gz=True)
        except (OSError, ValueError, EOFError) as e:
            raise PodError(f"The saved draft in {os.path.basename(folder)} can't be read ({type(e).__name__}).") from e
        if state.get("v") != POD_VERSION or raw.get("v") != POD_VERSION:
            raise PodError("This saved draft is from another version of Manticore.")
        fields = raw.get("fields") or []
        try:
            cards = [cs.CubeCard(**{f: (tuple(v) if isinstance(v, list) else v) for f, v in zip(fields, row)})
                     for row in raw.get("cards") or []]
            draft = dc.Draft.from_dict(state["draft"])
        except (TypeError, KeyError, dc.DraftError) as e:
            raise PodError(f"The saved draft in {os.path.basename(folder)} doesn't add up ({e}).") from e
        if draft.n_cards != len(cards):
            raise PodError("The saved draft's cards don't match its picks.")
        return cls(folder, state.get("cube") or {}, cards, state.get("flagged"), state.get("missing"), draft,
                   state.get("created") or 0, state.get("stage") or "drafting", state.get("deck_ids"),
                   style=state.get("style") or "commander", basics=state.get("basics") or (), rules=state.get("rules") or ())

    # ---- the draft --------------------------------------------------------------------------------------------------------
    @property
    def z(self):
        if self._z is None:
            self._z = dd.zscores(self.cards)
        return self._z

    @property
    def classic(self):
        """Round CD3: a classic draft (one card a pick, 40-card decks, 20 life, 1v1)."""
        return self.style == "classic"

    @property
    def format(self):
        """The format its decks are saved as: "commander_draft", or (round CD3) "draft"."""
        return CLASSIC_FORMAT if self.classic else FORMAT

    @property
    def station(self):
        """Round CD3: {colour: basic land name} - the cube's own basics (cube_source.basic_station)."""
        return cs.basic_station(list(self.basics))

    @property
    def chooser(self):
        if self._chooser is None:
            colours = "".join(c for c in self.station if c) if self.classic else "WUBRG"
            self._chooser = da.chooser(self.cards, self.draft.seed, self.z, self.style, colours)
        return self._chooser

    def run_ai(self):
        """The AI drafters pick while a pack is in front of them (until it is your turn, or the draft is over)."""
        return dc.run_ai(self.draft, self.chooser)

    @property
    def done(self):
        return self.draft.done

    def pack(self):
        """The cards (ints) of the pack in front of you, or None (the draft is over)."""
        return self.draft.pack_for(0)

    def needed(self):
        return self.draft.needed(0)

    def label(self):
        return self.draft.label(0)

    def passing(self):
        """(direction word, the seat your pack goes to): ("left", "AI 1") in packs 1 and 3, ("right", "AI 3") in pack 2."""
        d = self.draft.direction()
        return ("left" if d > 0 else "right"), SEAT_NAMES[self.draft.next_seat(0)]

    def pick(self, cards):
        """You take these cards (ints) from your pack; the AIs pick on; the pod is saved. Raises draft_core.DraftError."""
        self.draft.pick(0, list(cards))
        self.run_ai()
        self.save()

    def pool(self, seat=0):
        return list(self.draft.pools[seat])

    def counts(self):
        """How many cards each seat has drafted (903.13d: that is all you may know of the others during the draft)."""
        return [len(p) for p in self.draft.pools]

    def progress_text(self):
        """"Commander Cube - Pack 2, pick 3 of 10" (or "... - draft complete")."""
        what = self.label() if not self.done else "draft complete, decks not saved yet"
        return f"{self.cube.get('name') or self.cube.get('id')} - {what}"

    # ---- the decks ----------------------------------------------------------------------------------------------------------
    def build(self, seat):
        """draft_deck.build for one seat (a Deck), or None when that seat drafted no card that can be a commander. Round CD3: a
        classic pod's deck is draft_deck.build_classic's (never None)."""
        avoid = self.flagged if seat else frozenset()
        if self.classic:
            return dd.build_classic(self.cards, self.pool(seat), self.z, avoid=avoid, station=self.station)
        return dd.build(self.cards, self.pool(seat), self.z, avoid=avoid)

    def build_all(self):
        return [self.build(s) for s in range(self.draft.n)]

    def problems(self, seat, deck):
        """draft_deck.check's sentences for a built deck (an empty list = a legal Commander Draft deck)."""
        main = list(deck.spells + deck.lands) + [n for n, c in deck.basics.items() for _ in range(c)]
        if self.classic:
            return dd.check_classic(self.cards, self.pool(seat), main, self.station)
        return dd.check(self.cards, self.pool(seat), list(deck.commanders), main)

    def deck_name(self, seat, deck):
        """"2026-10-10 draft - You - The Scarab God" / "2026-10-10 draft - AI 1 - Urza"."""
        day = datetime.date.fromtimestamp(self.created or time.time()).isoformat()
        if self.classic:                                  # round CD3: "2026-10-12 draft - AI 2 - Blue-Red"
            return f"{day} draft - {SEAT_NAMES[seat]} - {dd.colour_name(deck.identity)}"
        names = [self.cards[i].name.split(",")[0].split(" // ")[0] for i in deck.commanders]
        return f"{day} draft - {SEAT_NAMES[seat]} - {' + '.join(names) or 'no commander'}"

    def deck_text(self, seat, deck):
        """The deck file: the format and draft lines, a note, then Commander and Deck sections (Moxfield's layout)."""
        commanders, main = dd.deck_names(self.cards, deck)
        counts = {}
        for n in main:
            counts[n] = counts.get(n, 0) + 1
        day = datetime.date.fromtimestamp(self.created or time.time()).strftime("%d %b %Y").lstrip("0")
        cube = self.cube.get("name") or self.cube.get("id")
        who = "you" if seat == 0 else SEAT_NAMES[seat]
        if self.classic:                                  # round CD3: a 40-card Draft deck, no Commander section
            lines = [formats.text_line(CLASSIC_FORMAT), f"# draft: {self.id}"]
            if self.rules:
                lines.append(house_rules.text_line(self.rules))
            lines += [f"# Draft of {cube} ({self.cube.get('id')}), {day}: drafted by {who}, built by Manticore's Auto-build.",
                      "", "Deck"]
            lines += [f"{k} {n}" for n, k in counts.items()]
            return "\n".join(lines) + "\n"
        lines = [formats.text_line(FORMAT), f"# draft: {self.id}",
                 f"# Commander Draft of {cube} ({self.cube.get('id')}), {day}: drafted by {who}, built by Manticore's Auto-build.",
                 "", "Commander"] + [f"1 {c}" for c in commanders] + ["", "Deck"]
        lines += [f"{k} {n}" for n, k in counts.items()]
        return "\n".join(lines) + "\n"

    def save_decks(self, decks, library_dir=None, base_dir=None):
        """Write every seat's deck to My decks (a free file name each). Returns their DeckEntry list, in seat order; the pod
        remembers their ids and is "saved". A seat whose deck is None (no commander drafted) gets None and no file."""
        import deck_library as lib
        library_dir = library_dir or lib.LIBRARY_DIR
        base_dir = base_dir or lib.BASE_DIR
        os.makedirs(library_dir, exist_ok=True)
        entries = []
        for seat, deck in enumerate(decks):
            if deck is None:
                entries.append(None)
                continue
            name = lib.free_name(self.deck_name(seat, deck), library_dir)
            path = os.path.join(library_dir, name + ".txt")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.deck_text(seat, deck))
            os.replace(tmp, path)
            entries.append(lib.DeckEntry(path, name, False, base_dir).load())
        self.deck_ids = [e.id if e is not None else None for e in entries]
        self.stage = "saved"
        self.save()
        return entries

    def discard(self):
        """Discard: the pod stays on disk (nothing is deleted) but is no longer offered to continue."""
        self.stage = "discarded"
        self.save()


def unfinished(root=None):
    """The newest pod whose decks aren't saved yet (and that can be read), or None."""
    root = root or pods_dir()
    try:
        names = sorted((n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n, STATE_FILE))), reverse=True)
    except OSError:
        return None
    for n in names:
        try:
            with open(os.path.join(root, n, STATE_FILE), "rb") as f:
                stage = json.loads(f.read()).get("stage")
        except (OSError, ValueError):
            continue
        if stage in ("saved", "discarded"):
            continue
        try:
            return Pod.load(os.path.join(root, n))
        except PodError:
            continue
    return None
