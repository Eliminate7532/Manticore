# SPDX-License-Identifier: GPL-3.0-or-later
"""
card_check.py - play every card of a deck through the real Forge engine, one card at a time, the way the table does, and report the ones that
go wrong: a question the table cannot answer, a click that does nothing, an engine error, or an answer the bridge had to make for you.

    python card_check.py sample_decks\\stompy_goreclaw.txt                            the whole deck (10 to 30 minutes)
    python card_check.py my_deck.txt --card "Force of Will" --card "Sol Ring"       only these
    python card_check.py my_deck.txt --out report.txt --json report.json

For each card it sets up a small board with Forge's own developer "Setup Game State" (twelve basic lands, a small library for each player, an opponent with a creature, an
artifact, an enchantment and a nonbasic land), then a scripted player casts the card (or plays it, for a land) and answers every question
by taking the first legal answer. Permanents are then set on the battlefield and clicked once per ability. Counterspells get a Lightning Bolt
on the stack to answer. It tests that the whole path works (table -> bridge -> Forge), NOT that the card does what its text says.

This needs Java and Forge (python setup_forge.py) like the game does. The game itself never starts the bridge in developer mode: only this
script and the tests do.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time

from allocation import Allocation
import forge_client as fc
import forge_log as flog
import forge_scripts as fs
from deck_loader import load_deck

BASICS = ["Plains", "Plains", "Island", "Island", "Swamp", "Swamp", "Mountain", "Mountain", "Forest", "Forest", "Forest", "Forest"] + \
    ["Plains", "Island", "Swamp", "Mountain"]     # Round CHK1: three of every colour ({B}{B}{B} - Underworld Dreams - couldn't be paid)
MY_CREATURE = "Grizzly Bears"                     # Round CHK1: a creature of mine on every board (pump, fight, "sacrifice a creature")
OPPONENT_BOARD = ["Grizzly Bears", "Sol Ring", "Sylvan Library", "Underground Sea",
                  "Gilded Lotus"]     # something to target: creature, artifact, enchantment, nonbasic land; Round UI3: mana value 4+ (Despark)
MY_GRAVEYARD = ["Hill Giant", "Gray Ogre"]   # Round UI3: creature cards to bring back (Victimize, reanimation)
ENCHANTED_ID = 9002                            # Round CHK2: a creature of mine that already wears an Aura (Daybreak Coronet:
ENCHANTED = ["Trained Armodon|Id:%d" % ENCHANTED_ID, "Holy Strength|AttachedTo:%d" % ENCHANTED_ID]   # "creature with another Aura")
CANCEL_GRACE = 1.0          # Round CHK2: seconds to wait for OK to light after the last target, before Cancel (< drive's 1.3 s idle)
COMBAT_TARGET = re.compile(r"ValidTgts\$[^|\n]*\b\w+\.(?:attacking|blocking)\b")   # Round CHK2: Eiganjo's Channel - needs combat
LIBRARY = ["Forest", "Island", "Swamp", "Mountain", "Plains"] * 3 + ["Grizzly Bears"] * 3     # Forge's setup EMPTIES a library that is not listed: a draw would lose the game and a search would find nothing
HAND_HELPERS = ["Brainstorm", "Forest"]        # cards to pitch or discard (Force of Will, Chrome Mox, Mox Diamond)
NOISE = re.compile(r"was not assigned to any set|Upcoming set|Read cards|Language '|ThreadUtil|Picked up|^\s*$|Adding it to UNKNOWN")
PROBLEM_LINE = re.compile(r"Exception|Error|ERROR|bridge:|Unknown key|Could not find|Unable to|at forge\.")

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"


# ---------------------------------------------------------------------------
# reading a card script
# ---------------------------------------------------------------------------
class Profile:
    """What the checker needs to know about one card, read from its Forge script."""

    def __init__(self, deck_name, script_text, commander=False):
        self.deck_name = deck_name
        self.commander = commander
        text = script_text or ""
        self.name = self._first(r"^Name:(.*)$", text) or deck_name
        if re.search(r"^AlternateMode:\s*Split\s*$", text, re.M):
            # Round 28d: Forge knows a split card (Fire // Ice, the Rooms: Funeral Room // Awakening Hall) by BOTH names
            # joined with " // ", not by its first Name: line - asking the set-up for "Funeral Room" set up nothing (soak
            # night 5's sweep). forge_client.forge_card_name does the same for decks since round 28ba.
            faces = [n.strip() for n in re.findall(r"^Name:(.*)$", text, re.M) if n.strip()]
            if len(faces) >= 2:
                self.name = " // ".join(faces[:2])
        self.cost = self._first(r"^ManaCost:(.*)$", text)
        self.types = self._first(r"^Types:(.*)$", text)
        self.is_land = "Land" in self.types.split() or (not self.types and "Land" in self.name)
        self.is_permanent = any(t in self.types.split() for t in ("Land", "Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"))
        self.abilities = len(re.findall(r"^A:", text, re.M))
        self.has_x = bool(re.search(r"\bX\b", self.cost)) or "X" in self.cost
        self.two_faced = bool(re.search(r"^AlternateMode:", text, re.M))
        self.needs_stack = bool(re.search(r"SP\$ Counter|AB\$ Counter|inZoneStack|TargetType\$ Spell|Defined\$ TargetedSpell|TgtZone\$ Stack", text))
        self.free_cast = bool(re.search(r"^S:.*AlternativeCost|^K:.*(Evoke|Prowl)|Cost\$ ExileFromHand|^S:Mode\$ Continuous.*Cost", text, re.M)) or self.cost in ("", "no cost")
        self.found = bool(script_text)
        # Round 29b: a planeswalker set up with no loyalty counters dies to state-based actions at once, and an Aura set up
        # attached to nothing goes to the graveyard - the sweep reported both as "the card is not where the setup put it"
        # (Tyvar, the Elspeths, Gideon, Ral; Conviction, Felidar Umbra, Gryff's Boon: 10 of every night's FAILs).
        self.loyalty = self._first(r"^Loyalty:(.*)$", text)
        self.is_aura = "Aura" in self.types.split()
        self.needs_combat = bool(COMBAT_TARGET.search(text))     # Round CHK2: only targets an attacking or blocking creature
        self.has_flash = bool(re.search(r"^K:Flash\b", text, re.M))

    @staticmethod
    def _first(rx, text):
        m = re.search(rx, text, re.M)
        return m.group(1).strip() if m else ""

    def lookup_names(self):
        """Names to try when Forge is asked for the card by name: the script's own name first, then the deck's."""
        names = [self.name]
        if self.deck_name not in names:
            names.append(self.deck_name)
        return names


def load_profiles(deck_file, runtime=None, only=None):
    """[Profile] for every different card of the deck (commanders first), skipping cards not in `only` when it is given."""
    commanders, deck = load_deck(deck_file)
    root = os.path.join(runtime or fc.DEFAULT_RUNTIME, "res", "cardsfolder")
    out, seen = [], set()
    for name in list(commanders) + list(deck):
        if name in seen:
            continue
        seen.add(name)
        if only and not any(o.lower() == name.lower() or o.lower() in name.lower() for o in only):
            continue
        text = find_script(root, name)
        if text is None:                                       # Round CHK1: a Secret Lair flavor name ("Chaos Theory" = Chaos Warp)
            real = flavor_names(runtime).get(name.lower())
            if real:
                text = find_script(root, real)
        out.append(Profile(name, text, commander=name in commanders))
    return out


def find_script(root, name):
    """The Forge script for a card name, or None. A double-faced or modal card's file is named by both faces
    ("malakir_rebirth_malakir_mire.txt"), so when the plain name isn't there the files starting with it are read for a
    first Name: line that matches (Round CHK1: 12 "no Forge script found" warnings in Karl's two decks were these)."""
    fn = fs.script_file_name(name)
    for folder in (fn[:1], "upcoming"):                        # cards of very new sets sit in cardsfolder/upcoming
        path = os.path.join(root, folder, fn + ".txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
    want = name.split("//")[0].split(" / ")[0].strip().lower()
    for folder in (fn[:1], "upcoming"):
        d = os.path.join(root, folder)
        try:
            cands = sorted(f for f in os.listdir(d) if f.startswith(fn + "_") and f.endswith(".txt"))
        except OSError:
            continue
        for c in cands:
            with open(os.path.join(d, c), encoding="utf-8", errors="replace") as f:
                text = f.read()
            m = re.search(r"^Name:(.*)$", text, re.M)
            if m and m.group(1).strip().lower() == want:
                return text
    return None


_FLAVOR = {}


def flavor_names(runtime=None):
    """{flavor name lower: real card name} from Forge's edition files (${"flavorName": "..."}), read once per runtime."""
    key = runtime or fc.DEFAULT_RUNTIME
    if key in _FLAVOR:
        return _FLAVOR[key]
    out = {}
    folder = os.path.join(key, "res", "editions")
    rx = re.compile(r'^\S+\s+\S+\s+(.+?)\s+(?:@[^$]*)?\$\{\s*"flavorName"\s*:\s*"([^"]+)"')
    try:
        files = os.listdir(folder)
    except OSError:
        files = []
    for fn in files:
        try:
            with open(os.path.join(folder, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "flavorName" in line:
                        m = rx.match(line.strip())
                        if m:
                            out.setdefault(m.group(2).strip().lower(), m.group(1).strip())
        except OSError:
            continue
    _FLAVOR[key] = out
    return out


# ---------------------------------------------------------------------------
# the board each check starts from
# ---------------------------------------------------------------------------
AURA_HOST_ID = 9001        # round 29b: the creature an Aura is attached to in an 'activate' set-up (Forge's "|Id:" / "|AttachedTo:")
AURA_HOST = "Grizzly Bears"


def setup_lines(profile, level, deck_commanders=()):
    """Forge 'Setup Game State' lines for one check. level: 'cast' (the card is in hand, or the command zone for a commander),
    'activate' (the card is already on the battlefield and no longer summoning sick) or 'stack' (as 'cast', with an opposing Lightning Bolt on the stack).
    deck_commanders (round 29b): the deck's commanders, put back in the command zone for every check - a set-up clears it, and a
    card that reads the commander (War Room: "life equal to the number of colors in your commanders' color identity") hit a
    NullPointerException in Forge with no commander at all."""
    name = profile.name
    battlefield = list(BASICS) + [MY_CREATURE] + ENCHANTED
    hand = list(HAND_HELPERS)
    if level == "activate":
        entry = name
        if getattr(profile, "loyalty", "") and profile.loyalty.isdigit():
            entry += "|Counters:LOYALTY=" + profile.loyalty
        if getattr(profile, "is_aura", False):
            battlefield.append("%s|Id:%d" % (AURA_HOST, AURA_HOST_ID))
            entry += "|AttachedTo:%d" % AURA_HOST_ID
        battlefield.append(entry)
    elif not profile.commander:
        hand.insert(0, name)
    opponent = list(OPPONENT_BOARD)
    ai_hand = ""
    active = "human"
    if level == "stack":
        # the opponent's own turn: it holds a Lightning Bolt and casts it, and the card being checked answers it. (Forge's 'put on stack' setup line
        # was tried first: it leaves the engine confused about who is paying, so a real cast is used instead.)
        active, ai_hand = "ai", "Lightning Bolt"
        opponent.append("Mountain")
        battlefield.append("Llanowar Elves")
    lines = ["humanlife=40", "ailife=40", "activeplayer=" + active, "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
             "humanhand=" + ";".join(hand), "humanbattlefield=" + ";".join(battlefield), "humanlibrary=" + ";".join(LIBRARY),
             "humangraveyard=" + ";".join(MY_GRAVEYARD),
             "aihand=" + ai_hand, "ailibrary=" + ";".join(LIBRARY), "aibattlefield=" + ";".join(opponent), "removesummoningsickness=true"]
    if profile.commander:
        lines.append("humancommand=%s|IsCommander" % name)          # every setup clears the commanders, so a commander is always put back
    elif deck_commanders:
        lines.append("humancommand=" + ";".join("%s|IsCommander" % c for c in deck_commanders))
    return lines


def levels_for(profile):
    """Which checks make sense for this card."""
    # Round CHK2: a creature without flash can't be cast in answer to the opponent's spell (Kitsa, Otterball Elite copies a spell
    # with an ability - the 'activate' check covers that); it is checked as an ordinary cast.
    stack = profile.needs_stack and not ("Creature" in profile.types.split() and not getattr(profile, "has_flash", False))
    levels = ["stack" if stack else "cast"]
    if profile.is_permanent and not profile.commander and (profile.abilities or profile.is_land or "{T}" in profile.types):
        levels.append("activate")
    elif profile.is_permanent and profile.abilities:
        levels.append("activate")
    return levels


# ---------------------------------------------------------------------------
# the scripted player: what to do next, from the state alone (no engine needed)
# ---------------------------------------------------------------------------
def prompt_key(message):
    """A short, id-free form of a prompt for the report: 'Sol Ring (203)  Pay Mana Cost: {1}' -> 'Sol Ring Pay Mana Cost: {1}'."""
    text = re.sub(r"\s*\(\d{1,5}\)", "", " ".join((message or "").split()))
    return text[:90]


def all_cards(state):
    """Every card the state shows, with the player it belongs to: [(card, zone_name, player_id)]."""
    out = []
    for p in state.get("players", []):
        for zone, cards in (p.get("zones") or {}).items():
            for c in cards:
                out.append((c, zone, p["id"]))
    for item in state.get("stack") or []:
        if item.get("card"):
            out.append((item["card"], "stack", item["card"].get("controller")))
    return out


def item_label(it):
    return str(it.get("label") or "") if it.get("kind") == "text" else ""


def is_heading(it):
    """A section heading Forge mixes into a list of choices, like --CARDS ON BATTLEFIELD:--."""
    return flog.is_heading(it)


def is_finish(it):
    return item_label(it).strip() == "[FINISH TARGETING]"


def request_answer(req, mem):
    """The answer a first-legal-option player gives to a request from the engine."""
    kind = req.get("kind")
    if kind == "confirm":
        return True
    if kind == "input":
        options = req.get("options") or []
        if options:
            return options[0]
        return "2" if req.get("numeric") else (req.get("initial") or "")
    if kind == "assign":                                               # combat damage / "divide N": the usual split
        alloc = Allocation.from_request(req)
        alloc.auto()
        return alloc.answer()
    items = req.get("items") or []
    lo, hi = req.get("min", 1), req.get("max", 1)
    if hi is None or hi < 0:
        hi = len(items)
    if kind in ("choose_optional",) and mem.get("ability_index") is not None and "ability" in (req.get("title") or "").lower():
        return [min(mem["ability_index"], len(items) - 1)] if items else []
    if kind == "order":
        return list(range(min(max(hi, lo), len(items))))
    if (req.get("title") or "").startswith("Choose X") and len(items) > 1:
        for i, it in enumerate(items):                                  # the opponent's spell is a Lightning Bolt: mana value 1
            if str(it.get("label") or "").strip() == "1":
                return [i]
    # Forge puts headings ("--CARDS ON BATTLEFIELD:--") and "[FINISH TARGETING]" into target lists: a player never picks a heading,
    # and picks "finish" only after choosing something.
    real = [i for i, it in enumerate(items) if not is_heading(it) and not is_finish(it)]
    finish = [i for i, it in enumerate(items) if is_finish(it)]
    chosen = mem.setdefault("chosen", {})
    title = req.get("title") or ""
    if finish and chosen.get(title, 0) >= 1:
        return finish[:1]
    chosen[title] = chosen.get(title, 0) + 1
    n = min(max(lo, 1), hi, len(real)) if real else 0
    return real[:n]


def next_move(state, requests, mem):
    """One thing to do now: ('answer', request, value) | ('click', card_id) | ('player', id) | ('ok',) | ('cancel',) | None (nothing to do).
    mem is the player's notebook (dict): it counts clicks so that a click that changes nothing is not repeated forever."""
    if requests:
        req = requests[0]
        return ("answer", req, request_answer(req, mem))
    if not state:
        return None
    prompt = state.get("prompt") or {}
    msg = prompt.get("message") or ""
    ok, cancel = prompt.get("ok") or {}, prompt.get("cancel") or {}
    me = next((p for p in state["players"] if p["id"] == state.get("me")), None)
    if not me:
        return None
    if "keep your hand" in msg.lower() or "coin toss" in msg.lower():
        return ("ok",)
    key = prompt_key(msg)
    seen = mem.setdefault("seen", {})
    if "Pay Mana Cost" in msg and mem.get("last_key") != key:
        seen[key] = 0                       # a new payment question, even when it reads exactly like an earlier one (three Flusterstorm copies each ask "pay {1}?")
    mem["last_key"] = key
    seen[key] = seen.get(key, 0) + 1
    if msg.startswith("Priority:"):
        if state.get("stack"):
            return ("ok",) if ok.get("enabled") else None
        return None
    if "Pay Mana Cost" in msg:
        if seen[key] > 7:
            mem["unpayable"] = key
            return ("cancel",) if cancel.get("enabled") else ("ok",)
        if seen[key] <= 2 and ok.get("enabled"):
            return ("ok",)                                              # the Auto button first, as a player in a hurry would
        sources = [c for c in me["zones"]["battlefield"] if (c.get("selectable") or c.get("weak")) and not c.get("tapped")]
        if sources and seen[key] <= 5:
            return ("click", sources[(seen[key] - 3) % len(sources)]["id"])
        return ("ok",) if ok.get("enabled") else (("cancel",) if cancel.get("enabled") else None)
    if prompt.get("selecting") or "elect" in msg or "hoose" in msg:
        clicked = mem.setdefault("clicked", {})
        options = [c for c, _z, _p in all_cards(state) if c.get("selectable")]
        source = re.sub(r"\s*\(\d+\)\s*$", "", msg.split(" - ", 1)[0]).strip() if " - " in msg else ""   # "Name (203) - ..."
        # opponents' cards first: they are the usual targets; the card asking comes last (Round CHK2: Izzet Boilerworks'
        # "Return a land you control" picked Izzet Boilerworks itself and left it in hand)
        options.sort(key=lambda c: (bool(source) and c.get("name") == source, c.get("controller") == state.get("me"), c["id"]))
        for c in options:
            if clicked.get((key, c["id"]), 0) < 1:
                clicked[(key, c["id"])] = 1
                return ("click", c["id"])
        opp = next((p for p in state["players"] if p["id"] != state.get("me")), None)
        if opp and not clicked.get((key, "p")) and "player" in msg.lower():
            clicked[(key, "p")] = 1
            return ("player", opp["id"])
        if ok.get("enabled"):
            mem.pop("cancel_wait", None)
            return ("ok",)
        if cancel.get("enabled"):
            # Round CHK2: every target clicked but OK not lit yet - the snapshot after the last click can lag behind it (Aether
            # Gale's six targets: Cancel half the time). Give Forge a second to light OK before giving up on the question.
            since = mem.setdefault("cancel_wait", {}).setdefault(key, time.time())
            if time.time() - since < CANCEL_GRACE:
                return None
            return ("cancel",)
        return None
    if ok.get("enabled"):
        return ("ok",) if seen[key] <= 3 else (("cancel",) if cancel.get("enabled") else None)
    return ("cancel",) if cancel.get("enabled") else None


# ---------------------------------------------------------------------------
# the checker (needs the engine)
# ---------------------------------------------------------------------------
class Result:
    def __init__(self, card, level):
        self.card, self.level = card, level
        self.status = OK
        self.notes = []
        self.prompts = []
        self.errors = []
        self.auto = []
        self.checks = []
        self.zone = ""
        self.pool = {}
        self.trace = []          # what the scripted player did, step by step (shown for failures and with --verbose)
        self.seconds = 0.0

    def note(self, text, status=None):
        self.notes.append(text)
        if status and (status == FAIL or self.status == OK):
            self.status = status

    def as_dict(self):
        return {"card": self.card, "level": self.level, "status": self.status, "notes": self.notes, "prompts": self.prompts,
                "errors": self.errors, "auto": self.auto, "checks": self.checks, "zone": self.zone, "trace": self.trace, "seconds": round(self.seconds, 1)}


class Checker:
    def __init__(self, deck_file, runtime=None, seed=7, say=print, per_check=40.0, faults=()):
        self.deck_file, self.runtime, self.seed, self.say, self.per_check = deck_file, runtime, seed, say, per_check
        self.faults = tuple(faults)         # round 27d: deliberate bridge mistakes, for tests that prove a check goes red
        self.tmp = tempfile.TemporaryDirectory(prefix="card_check_")
        self.s = None
        self.err_offset = 0
        self.restarts = 0

    # -- session --
    def start(self):
        commanders, deck = load_deck(self.deck_file)
        self.commanders = [fc.forge_card_name(c) for c in commanders]          # round 29b: put back in the command zone for every check
        mine = fc.write_deck_file(os.path.join(self.tmp.name, "me.dck"), commanders, deck, "Check")
        filler = fc.write_deck_file(os.path.join(self.tmp.name, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Filler")
        fc.sync_bridge(self.runtime) if self.runtime else fc.sync_bridge()
        self.s = fc.ForgeSession(mine, [filler], name="Checker", seed=self.seed, runtime=self.runtime, dev=True, faults=self.faults)
        self.s.stderr_path = os.path.join(self.tmp.name, f"engine_{self.restarts}.log")
        self.s.start()
        self.err_offset = 0
        end = time.time() + 150
        mem = {}
        while time.time() < end:
            self.s.poll()
            st = self.s.state
            if self.s.exited or self.s.fatal:
                raise RuntimeError(self.s.fatal or "Forge stopped while starting")
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                return True
            move = next_move(st, [], mem) if st else None
            if move and move[0] == "ok":
                self.s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        raise RuntimeError("the game did not start within 150 seconds")

    def stop(self):
        if self.s:
            self.s.close()
        self.tmp.cleanup()

    def restart(self):
        self.restarts += 1
        self.say("  (restarting the engine)")
        if self.s:
            self.s.close()
        return self.start()

    # -- engine log --
    def new_engine_lines(self):
        path = self.s.stderr_path
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.err_offset)
                text = f.read()
                self.err_offset = f.tell()
        except OSError:
            return []
        return [ln.strip() for ln in text.splitlines() if ln.strip() and not NOISE.search(ln)]

    # -- waiting --
    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.s.poll()
            time.sleep(0.02)

    def wait_change(self, version, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self.s.poll()
            if self.s.state_version != version or self.s.requests:
                return True
            time.sleep(0.02)
        return False

    def at_rest(self):
        st = self.s.state
        msg = ((st or {}).get("prompt") or {}).get("message", "")
        return bool(st) and msg.startswith("Priority:") and not st.get("stack") and not self.s.requests

    # -- moves --
    def do(self, move):
        s = self.s
        kind = move[0]
        if kind == "answer":
            s.answer(move[1], move[2])
        elif kind == "click":
            s.click_card(move[1])
        elif kind == "player":
            s.click_player(move[1])
        elif kind == "ok":
            s.ok()
        elif kind == "cancel":
            s.cancel()

    def drive(self, result, first=None, budget=None, ability_index=None):
        """Let the scripted player act until nothing is left to do. Records every prompt it met in result.prompts."""
        s = self.s
        mem = {"ability_index": ability_index}
        budget = budget or self.per_check
        t0 = time.time()
        idle_since = None
        stuck = 0
        if first:
            # Round CHK2: in a 'stack' check the AI's own priority passes can move Forge to a new question between the snapshot and
            # the click, and the bridge drops a click made on an old question (Round 22) - the card then "did nothing" (a different
            # counterspell each run of the Kinnan sweep). A dropped first click is sent again, up to 3 times, on the next snapshot.
            for _try in range(4):
                dropped = len(getattr(s, "dropped", []) or [])
                version = s.state_version
                first()
                self.pump(0.4)
                if len(getattr(s, "dropped", []) or []) <= dropped:
                    break
                self.wait_change(version, 1.0)
        while True:
            s.poll()
            now = time.time()
            if s.exited or s.fatal:
                result.note("the engine stopped: " + (s.fatal or "exited"), FAIL)
                return False
            if now - t0 > budget:
                self.stall(result, "took more than %d seconds" % budget)
                return False
            st = s.state
            move = next_move(st, list(s.requests), mem)
            if move is None:
                idle_since = idle_since or now
                if now - idle_since >= 1.3:
                    return True
                time.sleep(0.03)
                continue
            idle_since = None
            if move[0] == "answer":
                req = move[1]
                label = "%s: %s" % (req.get("kind"), prompt_key(req.get("title")))
                if req.get("kind") == "choose_optional" and "ability" in (req.get("title") or "").lower():
                    mem["abilities"] = [((it.get("label") or (it.get("card") or {}).get("name") or "?")[:60]) for it in req.get("items", [])]
                    result.note("asked which ability: " + " | ".join(mem["abilities"]))
                if label not in result.prompts:
                    result.prompts.append(label)
            else:
                label = "prompt: " + prompt_key(((st or {}).get("prompt") or {}).get("message"))
                if label not in result.prompts and not label.startswith("prompt: Priority"):
                    result.prompts.append(label)
            version = s.state_version
            if len(result.trace) < 60:
                result.trace.append(self.describe(move, st))
            self.do(move)
            if not self.wait_change(version, 1.6):
                stuck += 1
                if stuck >= 3:
                    self.stall(result, "no reaction to " + repr(move[0]))
                    return False
            else:
                stuck = 0
            if mem.get("unpayable"):
                result.note("could not pay: " + mem["unpayable"], WARN)
                mem.pop("unpayable")
        return True

    def describe(self, move, st):
        prompt = ((st or {}).get("prompt") or {})
        what = {"answer": lambda: "answer %s %r -> %r" % (move[1].get("kind"), prompt_key(move[1].get("title")), move[2]),
                "click": lambda: "click card %s (%s)" % (move[1], next((c.get("name") for c, _z, _p in all_cards(st or {}) if c["id"] == move[1]), "?")),
                "player": lambda: "click player %s" % move[1], "ok": lambda: "OK", "cancel": lambda: "Cancel"}[move[0]]()
        return "%s   [prompt: %s | stack %d]" % (what, prompt_key(prompt.get("message"))[:60], len((st or {}).get("stack") or []))

    def stall(self, result, why):
        st = self.s.state or {}
        prompt = st.get("prompt") or {}
        req = list(self.s.requests)[:1]
        result.note("stalled (%s) at: %s [ok=%s cancel=%s selecting=%s request=%s]" % (
            why, prompt_key(prompt.get("message")), (prompt.get("ok") or {}).get("label"), (prompt.get("cancel") or {}).get("label"),
            prompt.get("selecting"), (req[0].get("kind") + ": " + prompt_key(req[0].get("title"))) if req else None), FAIL)

    def clean(self):
        """Back to a plain priority with an empty stack; restarts the engine when that is not possible."""
        result = Result("(clean)", "")
        for _ in range(3):
            if self.at_rest():
                return True
            self.drive(result, budget=12.0)
            if self.at_rest():
                return True
            self.s.cancel()
            self.pump(0.5)
        return self.restart() and self.at_rest()

    def apply(self, lines, expect_hand, target=None):
        """Send the setup lines and wait until the hand shows what they asked for. The setup itself can ask questions (a shock land: pay 2 life?;
        Mox Diamond: discard a land) because putting a card on the battlefield runs its replacement effects: those are answered like a player would.
        When `target` is given (a card that goes straight onto the battlefield) finding it there counts too, since answering may change the hand."""
        self.s.setup(lines)
        t0 = time.time()
        end = t0 + 10
        while time.time() < end:
            self.s.poll()
            me = self.s.me()
            msg = ((self.s.state or {}).get("prompt") or {}).get("message", "")
            hand_ok = bool(me) and sorted(c["name"] for c in me["zones"]["hand"]) == sorted(expect_hand)
            asking = bool(msg) and not msg.startswith("Priority:") and time.time() - t0 > 1.5
            if hand_ok or asking:
                self.pump(0.6)
                tmp = Result("(setup)", "")
                self.drive(tmp, budget=8.0)
                me = self.s.me()
                if me and sorted(c["name"] for c in me["zones"]["hand"]) == sorted(expect_hand):
                    return True
                return bool(target) and self.find_in(target, "battlefield") is not None
            time.sleep(0.05)
        return False

    def wait_for_opponent_spell(self, seconds=12.0):
        end = time.time() + seconds
        while time.time() < end:
            self.s.poll()
            st = self.s.state or {}
            msg = (st.get("prompt") or {}).get("message", "")
            if st.get("stack") and msg.startswith("Priority:"):
                return True
            time.sleep(0.05)
        return False

    # -- one card --
    def find_in(self, name, zone, mine=True):
        st = self.s.state
        for c, z, pid in all_cards(st):
            if z == zone and (pid == st["me"]) == mine and name.lower() in ((c.get("name") or "").lower(), (c.get("oracleName") or "").lower()):
                return c
        return None

    def copies_in(self, name, zone, mine=True):
        st = self.s.state
        return sum(1 for c, z, pid in all_cards(st) if z == zone and (pid == st["me"]) == mine
                   and name.lower() in ((c.get("name") or "").lower(), (c.get("oracleName") or "").lower()))

    def where_is(self, name):
        st = self.s.state
        for c, z, pid in all_cards(st):
            if name.lower() in ((c.get("name") or "").lower(), (c.get("oracleName") or "").lower()) and c["id"] >= 0:
                yield ("my " if pid == st["me"] else "opponent's ") + z

    def check(self, profile, level, ability_index=None):
        result = Result(profile.deck_name, level + ("" if ability_index is None else " #%d" % (ability_index + 1)))
        t0 = time.time()
        if not profile.found:
            result.note("no Forge script found for this card name", WARN)
        if not self.clean():
            result.note("could not get back to a clean board", FAIL)
            return result
        lines = setup_lines(profile, level, getattr(self, "commanders", ()))
        hand = list(HAND_HELPERS) + ([] if level == "activate" or profile.commander else [profile.name])
        self.new_engine_lines()
        if not self.apply(lines, hand, profile.name if level == "activate" else None):
            errs = self.new_engine_lines()
            result.note("the board could not be set up (Forge does not know the card by the name %r?) %s" % (profile.name, "; ".join(errs[:2])), FAIL)
            result.errors = errs[:5]
            return result
        cast = True
        if level == "stack":
            self.s.ok()                     # the setup left me holding priority in the opponent's main phase: pass, and it casts its Bolt
            cast = self.wait_for_opponent_spell()
            for _again in range(2):         # Forge's AI sometimes keeps its Bolt for later: set the board up again, it decides afresh
                if cast or not (self.clean() and self.apply(lines, hand, None)):
                    break
                self.s.ok()
                cast = self.wait_for_opponent_spell()
        if level == "stack" and not cast:
            result.note("the opponent did not cast its Lightning Bolt, so there was nothing to answer", SKIP)
            result.seconds = time.time() - t0
            return result
        target = profile.name
        if level == "activate":
            card = self.find_in(target, "battlefield")
            first = (lambda: self.s.click_card(card["id"])) if card else None
        elif profile.commander:
            card = self.find_in(target, "command")
            first = (lambda: self.s.click_card(card["id"])) if card else None
        else:
            card = self.find_in(target, "hand")
            first = (lambda: self.s.click_card(card["id"])) if card else None
        if not card:
            result.note("the card is not where the setup put it (%s)" % target, FAIL)
            return result
        card_id = card["id"]
        self.drive(result, first, ability_index=ability_index)
        lines_out = self.new_engine_lines()
        result.auto = [ln for ln in lines_out if "AUTO" in ln or "auto_answered" in ln]
        result.checks = [ln for ln in lines_out if "CHECK FAILED" in ln and "auto_answered" not in ln]      # round 27d: the bridge broke one of its rules
        result.errors = [ln for ln in lines_out if PROBLEM_LINE.search(ln) and "AUTO" not in ln and "CHECK FAILED" not in ln][:6]
        zones = sorted(set(self.where_is(target)))
        result.zone = ", ".join(zones)
        me = self.s.me()
        result.pool = dict(me.get("manaPool") or {}) if me else {}
        if result.auto:
            result.note("the bridge answered for you: " + "; ".join(a.replace("bridge: AUTO ", "").replace("bridge: CHECK FAILED ", "") for a in result.auto[:2]), WARN)
        if result.checks:
            broke = [c for c in result.checks if "client_answer_unusable" not in c]
            result.note("bridge self-check: " + " / ".join(c.replace("bridge: CHECK FAILED ", "") for c in result.checks[:2]), FAIL if broke else WARN)
        if result.errors:
            result.note("engine messages: " + " / ".join(result.errors[:2]), FAIL if any("Exception" in e for e in result.errors) else WARN)
        if level == "activate":
            if not result.prompts and not result.pool and self.s.state_version and not zones:
                result.note("clicking it did nothing visible", WARN)
        elif level in ("cast", "stack"):
            if profile.commander:
                in_hand = "my command" in zones
            else:                                            # a Forest or Brainstorm is also one of the helper cards, so count the copies
                in_hand = self.copies_in(profile.name, "hand") > HAND_HELPERS.count(profile.name)
            if in_hand and getattr(profile, "needs_combat", False):
                # Round CHK2: Eiganjo's Channel targets only an attacking or blocking creature, and the check's board has no combat
                result.note("needs an attacking or blocking creature to target - the check's board has no combat", SKIP)
            elif in_hand and not any(p.startswith(("prompt: Pay", "choose", "confirm")) for p in result.prompts):
                result.note("clicking the card did nothing (could not be cast here)", WARN)
            elif in_hand:
                result.note("the card is still in hand after the check", WARN)
        if result.pool:
            result.note("floating mana afterwards: " + ", ".join("%s%s" % (v, k) for k, v in result.pool.items()))
        result.seconds = time.time() - t0
        return result

    def check_card(self, profile):
        results = []
        for level in levels_for(profile):
            first = self.check(profile, level)
            results.append(first)
            for note in first.notes:
                m = re.match(r"asked which ability: (.*)", note)
                if m:
                    options = m.group(1).split(" | ")
                    for i in range(1, min(len(options), 4)):
                        results.append(self.check(profile, level, ability_index=i))
                    break
        return results


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def collapse_repeats(steps):
    """The same step over and over (a stalled prompt asked 40 times) becomes one line 'step  (x40)'."""
    out, prev, n = [], None, 0

    def flush():
        if prev is not None:
            out.append(prev if n == 1 else "%s  (x%d)" % (prev.split("   [")[0], n))
    for step in steps:
        if step == prev:
            n += 1
            continue
        flush()
        prev, n = step, 1
    flush()
    return out


def format_report(results, deck_file, verbose=False):
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    lines = ["Card check of %s" % os.path.basename(deck_file),
             "  " + ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items())) + "  (%d checks)" % len(results),
             "  OK = did what a player would expect, WARN = worth a look, FAIL = stalled / error / board could not be set up", ""]
    for status in (FAIL, WARN, SKIP, OK):
        rows = [r for r in results if r.status == status]
        if not rows:
            continue
        lines.append("== %s (%d)" % (status, len(rows)))
        for r in rows:
            lines.append("- %s [%s]%s" % (r.card, r.level, "  -> " + r.zone if r.zone else ""))
            for n in r.notes:
                lines.append("      " + n)
            if r.prompts and (status != OK or verbose):
                lines.append("      prompts: " + " ; ".join(r.prompts[:6]))
            if r.trace and (status == FAIL or verbose):
                lines.extend("        > " + t for t in collapse_repeats(r.trace))
        lines.append("")
    kinds = {}
    for r in results:
        for p in r.prompts:
            k = p.split(":")[0]
            kinds[k] = kinds.get(k, 0) + 1
    lines.append("Kinds of questions met: " + ", ".join("%s x%d" % kv for kv in sorted(kinds.items())))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Play each card of a deck through the real Forge engine and report what goes wrong.")
    ap.add_argument("deck", help="a deck list (Moxfield text export)")
    ap.add_argument("--card", action="append", help="only cards whose name contains this (repeatable)")
    ap.add_argument("--runtime", default=None, help="folder holding forge.jar (default: the game's forge_runtime)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true", help="list every step the scripted player took, for every check")
    ap.add_argument("--out", default=None, help="write the text report here")
    ap.add_argument("--json", default=None, help="write the results as JSON here")
    args = ap.parse_args(argv)
    problem = fc.runtime_problem(args.runtime) if args.runtime else fc.runtime_problem()
    if problem:
        print("Forge is not ready: " + problem)
        return 2
    profiles = load_profiles(args.deck, args.runtime, args.card)
    if not profiles:
        print("No cards to check.")
        return 2
    print("Checking %d cards; the engine takes a while to start..." % len(profiles))
    checker = Checker(args.deck, args.runtime, args.seed)
    results = []
    try:
        checker.start()
        for i, prof in enumerate(profiles, 1):
            rows = checker.check_card(prof)
            results.extend(rows)
            print("[%d/%d] %-42s %s" % (i, len(profiles), prof.deck_name[:42], " ".join("%s:%s" % (r.level, r.status) for r in rows)), flush=True)
    finally:
        checker.stop()
    report = format_report(results, args.deck, args.verbose)
    print("\n" + report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([r.as_dict() for r in results], f, indent=1)
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
