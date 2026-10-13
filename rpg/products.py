# SPDX-License-Identifier: GPL-3.0-or-later
"""What the player opens (RPG plan decisions 11 and 12).

Jumpstart half-decks - the starting deck is three of them (decision 11: pick 3 of 363). Read at run time from Forge's own data:
res/blockdata/boosters-special.txt names each half-deck ("JMP Above the Clouds 1: 1 wholeSheet(...)") and res/blockdata/
printsheets.txt holds its card list under "[JMP Above the Clouds 1]" ("1 Warden of Evos Isle|JMP", "7 Island|JMP|3"). Jumpstart
(2020), Jumpstart 2022 and Jumpstart 2025: 121 half-decks each. Most hold 20 cards; a few hold 19, 22 or 23 (Forge's lists as they
are). A starting deck of three that comes to fewer than 60 is filled up with its own most common basic land (starter_deck).

Booster packs - opened by Forge's own BoosterGenerator at BUILD time (tools/rpg_build_data.py packs ...) and kept as a pool per set
in rpg/data/packs/<SET>.json: a shop's pack is one drawn from that pool. The game never starts Java to open a pack.
"""
import json
import os
import random
import re

from rpg import cardpool, data_path, rules

PACKS_DIR = data_path("packs")
JUMPSTART_SETS = ("JMP", "J22", "J25")
SET_NAMES = {"JMP": "Jumpstart", "J22": "Jumpstart 2022", "J25": "Jumpstart 2025"}
_SHEET_RE = re.compile(r"^\[(.+)\]\s*$")
_PACK_RE = re.compile(r'^((?:%s) [^:]+):\s*1 wholeSheet\("([^"]+)"\)' % "|".join(JUMPSTART_SETS))
_LINE_RE = re.compile(r"^(\d+)\s+([^|]+?)\s*(?:\|.*)?$")


class HalfDeck:
    """One Jumpstart half-deck: code "J22 Constellation 1", set "J22", theme "Constellation", number 1 (0 when the theme has one)."""
    __slots__ = ("code", "set", "theme", "number", "cards")

    def __init__(self, code, cards):
        self.code = code
        self.set, _, rest = code.partition(" ")
        m = re.match(r"^(.*?)\s+(\d+)$", rest)
        self.theme, self.number = (m.group(1), int(m.group(2))) if m else (rest, 0)
        self.cards = cards                  # [(name, count)] in Forge's order

    @property
    def size(self):
        return sum(k for _n, k in self.cards)

    def basics(self, pool):
        return [(n, k) for n, k in self.cards if (pool.get(n) is not None and pool.get(n).is_basic)]

    def __repr__(self):
        return f"HalfDeck({self.code!r}, {self.size} cards)"


def read_sheets(path):
    """{sheet name: [line, ...]} from printsheets.txt (names stripped: Forge's file has "[J25 Giddyap ]")."""
    sheets, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            m = _SHEET_RE.match(line)
            if m:
                cur = m.group(1).strip()
                sheets[cur] = []
            elif cur is not None and line and not line.startswith("#"):
                sheets[cur].append(line)
    return sheets


def read_jumpstart(runtime=None):
    """[HalfDeck] in Forge's order, and [(code, why)] for any it couldn't read (never silently dropped)."""
    runtime = runtime or rules.runtime_dir()
    folder = os.path.join(runtime, "res", "blockdata")
    sheets = read_sheets(os.path.join(folder, "printsheets.txt"))
    decks, problems = [], []
    with open(os.path.join(folder, "boosters-special.txt"), encoding="utf-8") as f:
        for line in f:
            m = _PACK_RE.match(line.strip())
            if not m:
                continue
            code, sheet = m.group(1).strip(), m.group(2).strip()
            lines = sheets.get(sheet)
            if lines is None:
                problems.append((code, f"no sheet named {sheet!r} in printsheets.txt"))
                continue
            cards = []
            for ln in lines:
                lm = _LINE_RE.match(ln)
                if not lm:
                    problems.append((code, f"unreadable line {ln!r}"))
                    continue
                cards.append((lm.group(2).strip(), int(lm.group(1))))
            decks.append(HalfDeck(code, cards))
    return decks, problems


_JUMPSTART = {}


def jumpstart(runtime=None):
    runtime = runtime or rules.runtime_dir()
    if runtime not in _JUMPSTART:
        _JUMPSTART[runtime] = read_jumpstart(runtime)[0]
    return _JUMPSTART[runtime]


def starter_deck(halves, pool=None, lists=None):
    """[(name, count)] for a starting deck of half-decks: their cards added up (a card in two of them counts twice), held to the
    deck rules (four of a card, or the card's own limit; one of a restricted card - three Jumpstart half-decks could hold six
    Archaeomenders), then filled up to 60 with the most common basic land among them when they hold fewer."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    total, order = {}, []
    for h in halves:
        for n, k in h.cards:
            if n not in total:
                order.append(n)
            total[n] = total.get(n, 0) + k
    for n in order:
        card = pool.get(n)
        if card is None or card.is_basic or card.copy_limit < 0:
            continue
        limit = 1 if lists.is_restricted(n) else (card.copy_limit or rules.FOUR)
        total[n] = min(total[n], limit)
    size = sum(total.values())
    if size < rules.MIN_DECK:
        basics = {}
        for h in halves:
            for n, k in h.basics(pool):
                basics[n] = basics.get(n, 0) + k
        if basics:
            fill = max(sorted(basics), key=lambda n: basics[n])
            total[fill] += rules.MIN_DECK - size
    return [(n, total[n]) for n in order]


def offer(rng, decks=None, n=3, exclude=()):
    """n half-decks to choose from, different themes, none of `exclude` (codes): the starting-deck screen shows a few at a time."""
    decks = list(decks if decks is not None else jumpstart())
    rng.shuffle(decks)
    out, themes = [], set()
    for d in decks:
        if d.code in exclude or d.theme in themes:
            continue
        out.append(d)
        themes.add(d.theme)
        if len(out) == n:
            break
    return out


# ---- booster packs -------------------------------------------------------------------------------------------------------

def pack_file(code):
    return os.path.join(PACKS_DIR, f"{code.upper()}.json")


def has_packs(code):
    return os.path.isfile(pack_file(code))


def read_pool(code):
    """{"set", "name", "template", "seed", "forge", "packs": [[[name, set, number, rarity(, foil)], ...], ...]}"""
    with open(pack_file(code), encoding="utf-8") as f:
        return json.load(f)


def open_pack(code, rng):
    """One pack of set `code`: [(name, rarity, foil)], drawn from its pool by `rng` (random.Random)."""
    data = read_pool(code)
    pack = rng.choice(data["packs"])
    return [(c[0], c[3], bool(c[4]) if len(c) > 4 else False) for c in pack]


def write_pools(java_out, folder, forge_version, sets_wanted):
    """Split the Java tool's boosters output into one rpg/data/packs/<SET>.json per set (build time). Returns {set: packs}."""
    os.makedirs(folder, exist_ok=True)
    done = {}
    for code in sets_wanted:
        entry = java_out["sets"].get(code)
        if not entry or "packs" not in entry:
            continue
        path = os.path.join(folder, f"{code.upper()}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"set": %s, "name": %s, "template": %s, "seed": %d, "forge": %s,\n "packs": [\n' % (
                json.dumps(code), json.dumps(entry["name"]), json.dumps(entry["template"]), java_out.get("seed", 0),
                json.dumps(forge_version)))
            f.write(",\n".join("  " + json.dumps(p, ensure_ascii=False) for p in entry["packs"]))
            f.write("\n]}\n")
        os.replace(tmp, path)
        done[code] = len(entry["packs"])
    return done


def pack_rng(seed):
    return random.Random(seed)
