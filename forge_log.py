# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_log.py - Forge's game-log lines turned into readable rows.

Forge writes its log for a programmer: "Karl cast Llanowar Elves", "Forest (44) - {T}: Add {G}.", "Life: Karl 40 > 39",
"Lotus Petal (70) was put into Graveyard from Battlefield.", "[Zone Changer: Chrome Mox (117)]", one line per step change...
This module rewrites those lines ("You cast Llanowar Elves", "Your life: 40 to 39 (-1)", "Lotus Petal died"), drops the noise,
puts a turn header before each turn and a small phase label before the first action of a phase, and cuts every row into
coloured pieces (you, opponents, card names ...) that the table can draw and wrap. No pygame in here: text measuring is
done with whatever font object the caller hands in (anything with .size(text) -> (w, h)).
"""
import re

# ---- what the pieces are called (the table maps each to a colour) ----
TEXT, ME, OPP, CARD, DIM, GOOD, BAD = "text", "me", "opp", "card", "dim", "good", "bad"
BOLD_STYLES = {ME, OPP, CARD}

ME_MARK, MY_MARK, LOW_MARK = "\x01", "\x02", "\x03"    # stand-ins for "You", "Your" and "you" while the text is being rewritten
HIDDEN_TYPES = {"MATCH_RESULTS", "GAME_OUTCOME_SUMMARY"}      # (MANA lines are dropped one by one in LogFormatter.format)

# Forge's phase texts -> the short label shown in the log (None = not worth a label)
PHASE_LABELS = [("Untap", None), ("Upkeep", "Upkeep"), ("Draw", "Draw"), ("Main phase, precombat", "Main 1"),
                ("Main phase, postcombat", "Main 2"), ("Beginning of Combat", "Combat"), ("Declare Attackers", "Attackers"),
                ("Declare Blockers", "Blockers"), ("First Strike Damage", "Damage"), ("Combat Damage", "Damage"),
                ("End of Combat", None), ("End step", "End step"), ("Cleanup", None)]
_PHASE_LINE = re.compile(r"^.+?'s (?P<rest>(?:Untap|Upkeep|Draw|Main|Beginning|Declare|First|Combat|End|Cleanup).*)$")

_BRACKETS = re.compile(r"\s*\[[^\]]*\]")
_IDS = re.compile(r"\s\(\d{1,5}\)")
_SPELL_TYPE = re.compile(r"^(?P<n>.+?) - (?:(?:Legendary|Snow|Basic|Tribal|Token|Artifact|Enchantment|Land|Creature|Instant|Sorcery|"
                         r"Planeswalker|Battle|Kindred)\b ?)+.*$")
_ACTION = re.compile(r"^(?P<a>\S.*?) (?P<v>cast|triggered|activated|played) (?P<c>.+?)(?: targeting(?: (?P<t>.+))?)?$")
_LIFE = re.compile(r"^Life: (?P<p>.+) (?P<a>-?\d+) > (?P<b>-?\d+)$")
_ATTACK = re.compile(r"^(?P<a>.+?) assigned (?P<what>.+) to attack (?P<d>.+?)\.?$")
_BLOCK = re.compile(r"^(?P<a>.+?) assigned (?P<what>.+) to block (?P<t>.+?)\.?$")
_NO_BLOCK = re.compile(r"^(?P<a>.+?) didn'?t block (?P<t>.+?)\.?$")
_MOVED = re.compile(r"^(?P<c>.+?) was put into (?P<to>\w+) from (?P<fr>\w+)\.?$")
_TURN = re.compile(r"^Turn (?P<n>\d+) \((?P<who>.*)\)$")
# "Elvish Spirit Guide - Exile Elvish Spirit Guide from your hand: Add {G}." Mana from a card that leaves your hand: it is the one
# payment you cannot see on the table (no tapped permanent), so unlike ordinary mana lines it stays in the log.
_HAND_MANA = re.compile(r"^(?P<c>.+?) - Exile .+? from your (?P<zone>hand|graveyard): Add (?P<m>.+?)\.?$")


class Row:
    """kind: 'turn' | 'phase' | 'line' | 'spacer'. For 'line': segs = [(text, style)], bar = colour name of the marker,
    actor = 'me' | 'opp' | None, feed = worth flashing on the board, card = the card it is about (for the spotlight)."""
    __slots__ = ("kind", "segs", "bar", "actor", "feed", "card", "label", "turn", "mine", "indent")

    def __init__(self, kind, segs=(), bar=None, actor=None, feed=False, card=None, label="", turn=0, mine=False, indent=0):
        self.kind, self.segs, self.bar, self.actor, self.feed, self.card = kind, list(segs), bar, actor, feed, card
        self.label, self.turn, self.mine, self.indent = label, turn, mine, indent

    def text(self):
        return "".join(t for t, _s in self.segs) or self.label

    def __repr__(self):
        return f"Row({self.kind}, {self.text()!r})"


def tidy(text):
    """Forge's log text without card ids ('Forest (44)') and without [bracketed debug tails]."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _BRACKETS.sub("", text)
    text = _IDS.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


_WORDS = re.compile(r"[a-z0-9{}+/']+")
_TRIGGER_WORD = re.compile(r"\b(when|whenever|at the beginning)\b", re.I)
_ACTIVATED = re.compile(r"^[^:\n]{1,80}:\s")            # "{T}: Add {G}."  "{2}, Sacrifice this: ..."  "+1: ..."
SAME_WORDING = 0.85                                     # this much overlap = Forge's text already says what the card says


def _word_set(text, name=""):
    text = tidy(text).lower()
    if name:
        text = text.replace(name.lower(), "~")
    return set(_WORDS.findall(text))


def printed_wording(item):
    """The card's own paragraph for a trigger or activated ability on the stack, or None.

    Forge works numbers out the moment the ability goes on the stack, before its own first step has happened, so The One Ring's
    tap says "draws zero cards" although the card says "draw a card for each burden counter". When the source card's rules text has a
    paragraph for this kind of ability and Forge's stack text says something noticeably different, this returns that paragraph so
    the table can show both. Nothing is returned for a spell, for a card whose text we do not have, or when the choice is unclear."""
    kind = "trigger" if item.get("trigger") else "ability" if item.get("ability") else None
    card = item.get("card") or {}
    if kind is None or not card.get("text"):
        return None
    name = card.get("name") or ""
    paras = [p.strip() for p in (card["text"] or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if p.strip()]
    if kind == "trigger":
        cands = [p for p in paras if _TRIGGER_WORD.search(p)]
    else:
        cands = [p for p in paras if _ACTIVATED.match(p) and not _TRIGGER_WORD.match(p)]
    if not cands:
        return None
    detail = tidy(item.get("text") or "").replace("\n", " ")
    if name and detail.lower().startswith(name.lower()):
        detail = detail[len(name):].lstrip(" :-")
    have = _word_set(detail, name)
    scored = []
    for p in cands:
        mine = _word_set(p, name)
        scored.append((len(have & mine) / len(have | mine) if have | mine else 0.0, p))
    scored.sort(key=lambda sp: -sp[0])
    best, para = scored[0]
    if len(scored) > 1 and (best < 0.25 or scored[1][0] == best):
        return None                                     # several abilities of this kind and no clear match
    return None if best >= SAME_WORDING else para


def strip_ids(text):
    """'Kinnan, Bonder Prodigy (155)' -> 'Kinnan, Bonder Prodigy' (Forge's card numbers mean nothing to a player)."""
    return _IDS.sub("", text or "")


_HEADING = re.compile(r"^--.*--$")


def is_heading(item):
    """True for a section heading Forge slips into a list of choices ('--CARDS ON BATTLEFIELD:--'): text to read, never a choice."""
    if not isinstance(item, dict) or item.get("kind") != "text":
        return False
    return bool(_HEADING.match(str(item.get("label") or "").strip()))


def phase_label(text):
    """('Karl's Main phase, precombat') -> ('Main 1', True). (None, True) = a phase we do not label; (None, False) = not a phase line."""
    m = _PHASE_LINE.match(text.strip())
    if not m:
        return None, False
    rest = m.group("rest")
    for prefix, label in PHASE_LABELS:
        if rest.lower().startswith(prefix.lower()):
            return label, True
    return None, True


class LogFormatter:
    def __init__(self):
        self.me = ""
        self.opps = []
        self.cards = frozenset()
        self._rx = None
        self._rx_key = None
        self.pending = None
        self.last = None

    # ---- context: who is who, which names are cards --------------------------------------------
    def context(self, me, opponents, card_names):
        key = (me, tuple(opponents), len(card_names))
        self.me, self.opps = me or "", [o for o in opponents if o]
        if key == self._rx_key:
            return
        self._rx_key = key
        self.cards = frozenset(n for n in card_names if n and len(n) > 2)
        names = sorted(self.cards, key=len, reverse=True) + sorted(self.opps, key=len, reverse=True)
        alt = "|".join(re.escape(n) for n in names)
        marks = f"([{ME_MARK}{MY_MARK}{LOW_MARK}])"
        self._rx = re.compile(marks + (rf"|(?<![\w'])({alt})(?![\w])" if alt else ""))

    # ---- one Forge entry -> zero or more rows ------------------------------------------------------
    def format(self, entry):
        typ, raw = entry.get("type", ""), entry.get("text", "")
        if typ in HIDDEN_TYPES:
            return []
        rows = []
        for line in tidy(raw).split("\n"):
            line = line.strip()
            if typ == "MANA" and not _HAND_MANA.match(line):        # ordinary mana lines ("Forest - {T}: Add {G}") are noise
                continue
            if line:
                rows.extend(self._one(typ, line))
        return rows

    def _me(self, text):
        if not self.me:
            return text
        text = re.sub(rf"(?<![\w']){re.escape(self.me)}'s\b", MY_MARK, text)
        text = re.sub(rf"(?<![\w']){re.escape(self.me)}(?![\w])", ME_MARK, text)
        text = re.sub(rf"\b(to|from|by|on|of|for|with|against|targeting|at|attack|block) {ME_MARK}", lambda m: f"{m.group(1)} {LOW_MARK}", text)   # "deals 2 damage to you"
        for wrong, right in ((f"{ME_MARK} has ", f"{ME_MARK} have "), (f"{ME_MARK} is ", f"{ME_MARK} are "),
                             (f"{ME_MARK} was ", f"{ME_MARK} were "), (f"{ME_MARK} receives ", f"{ME_MARK} receive "),
                             (f"{ME_MARK} gains ", f"{ME_MARK} gain "), (f"{ME_MARK} loses ", f"{ME_MARK} lose "),
                             (f"{ME_MARK} draws ", f"{ME_MARK} draw ")):
            text = text.replace(wrong, right)
        return text

    def _actor(self, text):
        if text.startswith((ME_MARK, MY_MARK, LOW_MARK)):
            return ME
        if any(text.startswith(o) for o in self.opps):
            return OPP
        return None

    def _one(self, typ, line):
        if typ == "PHASE":
            label, is_phase = phase_label(line)
            if is_phase:
                self.pending = label
            return []
        m = _TURN.match(line) if typ == "TURN" else None
        if m:
            self.pending = self.last = None
            who = m.group("who")
            return [Row("spacer"), Row("turn", turn=int(m.group("n")), label=who, mine=(who == self.me))]
        if "picked {" in line:                                  # Forge noting which colour a mana source made
            return []
        text = self._me(line)
        rows = []
        if typ not in ("MULLIGAN", "PLAYER_CONTROL") and self.pending and self.pending != self.last:
            rows.append(Row("phase", label=self.pending))
            self.last = self.pending
        rows.append(self._line(typ, text))
        return [r for r in rows if r is not None]

    # ---- the row for one line -------------------------------------------------------------------
    def _line(self, typ, text):
        actor = self._actor(text)
        bar = typ
        if typ == "MANA":
            m = _HAND_MANA.match(text)
            if m:
                return Row("line", [(m.group("c"), CARD), (f" exiled from {m.group('zone')} for {m.group('m')}", TEXT)],
                           bar, None, feed=True, card=m.group("c"))
        m = _ACTION.match(text) if typ in ("STACK_ADD", "LAND") else None
        if m:
            a, verb, card, tgt = m.group("a"), m.group("v"), m.group("c"), m.group("t")
            segs = self._tokens(a) + [(f" {verb} ", TEXT), (card, CARD)]
            if tgt:
                segs += [(" targeting ", TEXT)] + self._tokens(tgt)
            return Row("line", segs, bar, self._actor(a) or actor, feed=self._actor(a) == OPP, card=card)
        if typ == "STACK_RESOLVE":
            return self._resolve(text, bar)
        if typ == "ZONE_CHANGE":
            m = _MOVED.match(text)
            if m:
                c, to, fr = m.group("c"), m.group("to"), m.group("fr")
                if to == "Graveyard" and fr == "Battlefield":
                    tail = " died"
                elif to == "Exile":
                    tail = f" was exiled from the {fr.lower()}"
                else:
                    tail = f" moved from the {fr.lower()} to the {to.lower()}"
                return Row("line", [(c, CARD), (tail, TEXT)], bar, None, feed=True, card=c)
        if typ == "LIFE":
            m = _LIFE.match(text)
            if m:
                who, a, b = m.group("p"), int(m.group("a")), int(m.group("b"))
                delta = b - a
                head = [("Your", ME)] if who in (ME_MARK, MY_MARK) or who == self.me else self._tokens(who) + [("'s", TEXT)]
                segs = head + [(f" life: {a} to {b} ", TEXT), (f"({delta:+d})", GOOD if delta > 0 else BAD)]
                return Row("line", segs, bar, ME if head[0][1] == ME else OPP, feed=True)
        if typ == "COMBAT":
            for rx, make in ((_ATTACK, self._attack), (_BLOCK, self._block), (_NO_BLOCK, self._no_block)):
                m = rx.match(text)
                if m:
                    segs = make(m)
                    return Row("line", segs, bar, self._actor(m.group("a")), feed=self._actor(m.group("a")) == OPP)
        if typ == "DAMAGE":
            segs = self._tokens(text.rstrip("."))
            return Row("line", segs, bar, None, feed=True)
        segs = self._tokens(text)
        return Row("line", segs, bar, actor, feed=False)

    def _plural(self, who):
        return who in (ME_MARK, MY_MARK) or who == self.me

    def _attack(self, m):
        a, what, d = m.group("a"), m.group("what"), m.group("d")
        verb = " attack " if self._plural(a) else " attacks "
        return self._tokens(a) + [(verb, TEXT)] + self._tokens(d) + [(" with ", TEXT)] + self._tokens(what)

    def _block(self, m):
        a, what, t = m.group("a"), m.group("what"), m.group("t")
        verb = " block " if self._plural(a) else " blocks "
        return self._tokens(a) + [(verb, TEXT)] + self._tokens(t) + [(" with ", TEXT)] + self._tokens(what)

    def _no_block(self, m):
        a, t = m.group("a"), m.group("t")
        verb = " don't block " if self._plural(a) else " doesn't block "
        return self._tokens(a) + [(verb, TEXT)] + self._tokens(t)

    def _resolve(self, text, bar):
        """'Llanowar Elves - Creature 1 / 1' -> 'Llanowar Elves resolves'; ability text is kept but shortened."""
        m = _SPELL_TYPE.match(text)
        if m:
            return Row("line", [(m.group("n"), CARD), (" resolves", DIM)], bar, None, indent=1)
        if text in self.cards or (len(text) <= 34 and not re.search(r"[.:;]|\b(you|your|target)\b", text)):
            return Row("line", [(text, CARD), (" resolves", DIM)], bar, None, indent=1)
        if len(text) > 150:
            text = text[:147].rsplit(" ", 1)[0] + "..."
        return Row("line", self._tokens(text, DIM), bar, self._actor(text), indent=1)

    # ---- colouring: who / which card ----------------------------------------------------------------
    def _tokens(self, text, base=TEXT):
        if not text:
            return []
        segs, pos = [], 0
        for m in (self._rx.finditer(text) if self._rx else ()):
            if m.start() > pos:
                segs.append((text[pos:m.start()], base))
            if m.group(1):
                segs.append(({ME_MARK: "You", MY_MARK: "Your", LOW_MARK: "you"}[m.group(1)], ME))
            else:
                name = m.group(2)
                segs.append((name, OPP if name in self.opps else CARD))
            pos = m.end()
        if pos < len(text):
            segs.append((text[pos:], base))
        return _fix_marks(_merge(segs))


def _fix_marks(segs):
    """Stray marker characters (a possessive inside a longer word) never reach the screen."""
    return [(t.replace(ME_MARK, "You").replace(MY_MARK, "Your").replace(LOW_MARK, "you"), s) for t, s in segs]


def _merge(segs):
    out = []
    for t, s in segs:
        if out and out[-1][1] == s and s in (TEXT, DIM):
            out[-1] = (out[-1][0] + t, s)
        else:
            out.append((t, s))
    return out


# ---- word wrapping of coloured pieces -----------------------------------------------------------------------

_WORDS = re.compile(r"\S+\s*|\s+")


def wrap_segments(segs, width, font, bold_font=None, first_indent=0, next_indent=0):
    """Cut a row's pieces into lines no wider than `width`. Each line is a list of (text, style, ref) where ref is the whole
    card name a card word belongs to (so a name wrapped over two lines is still one hover target)."""
    bold_font = bold_font or font
    tokens = []
    for text, style in segs:
        ref = text if style == CARD else None
        for w in _WORDS.findall(text):
            tokens.append((w, style, ref))
    lines, cur, x = [], [], first_indent
    limit = width
    for tok in tokens:
        w, style, ref = tok
        f = bold_font if style in BOLD_STYLES else font
        tw = f.size(w.rstrip())[0]
        if cur and x + tw > limit:
            _trim(cur)
            lines.append(cur)
            cur, x = [], next_indent
            if not w.strip():
                continue
        cur.append(tok)
        x += f.size(w)[0]
    if cur:
        _trim(cur)
        lines.append(cur)
    return lines or [[]]


def _trim(line):
    if line:
        t, s, r = line[-1]
        line[-1] = (t.rstrip(), s, r)
