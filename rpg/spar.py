# SPDX-License-Identifier: GPL-3.0-or-later
"""Sparring in Lantern Hollow (Round SPAR1; Karl, 3 Nov 2026: "B as its own task after prog-tune" - the handover's section 8, option
B): a measured win chance for the player's OWN deck, with its range.

Card quality (Round PROG1b) is how often tournament players use the cards, not how strong a deck is (RPG-SOAK1 section 4.3: the
same bot won 69% with its starting deck and 58% after rebuilding it by tier). Sparring measures instead: the yard's master plays N
practice duels - Forge's AI on BOTH seats, the player's current deck with the player's level and gear start effects on its seat
(rpg.levels.duel_effects, the same dict the table gives the bridge), against this region's regular foes - and says how many it won,
with a Wilson 95% range:

    "Won 26 of 40 against the moor's foes (65%; likely 50-78%)."

The foes: every kind a regular encounter on this region's maps uses (the moor's Ember Bandit, Grave Robber, Thornback and Zealot in
region 1), each with the deck list the region's regular fights use (towns.json enemy_decks: region 1 plays the Jumpstart decks,
their handicaps already in the lists - rpg.enemies), spread evenly: duel i is against kind i mod K (kinds sorted), and each kind's
decks are dealt in a shuffled order, one after another. Everything is drawn from `seed`, so the same deck, effects and seed give the
same pairings and the same Forge seeds (Forge's AI has time limits, so two runs can still differ - SPAR1's doc measures how much).

The duels run on a background thread through java_rpg's forge.rpgtools.Duels with its pairings on standard input (Round RPG-SOAK1:
one Java start - Forge's model, about 25 s - for all the duels). The window never waits on it: Sparring.progress() is read each
frame; stop() ends it at once (the duel being played is dropped; every finished one counts).

Sparring gives no gold, cards or XP and moves no clock (SPAR1's reading: a measurement, not a farm). The result is kept in the save
("sparring": a list of records, newest last, rpg.save) and the deck editor shows the deck's last ones beside Card quality.

    settings()                  progression.json "sparring": duels (default 40), max_duels, timeout, against (a region's words)
    region_foes(save)           [(kind id, name, source, [decks])] - this region's regular foes and the decks their fights use
    plan(save, n, seed)         [{"kind", "name", "index", "cards", "seed"}] - the duels, spread over the kinds
    deck_cards(deck)            the deck's main list as card names, copies repeated
    start_spec(save)            the player's start effects as the duel tool's SPEC ("" = none)
    Sparring(save, n, seed)     the run: start(), progress(), stop(), finished, result()
    wilson(w, n)                the 95% range
    result_lines(res)           the words of the result window
    record(save, res, now)      the result into the save (per deck: name, fingerprint, day, duels, wins)
    history(save, deck)         that deck's records, newest first
    history_lines(save, deck)   "Last sparred: Day 4, 65% (50-78%)" (+ "Before: ..."), for the deck editor
"""
import hashlib
import json
import math
import os
import random
import subprocess
import tempfile
import threading
import time

from rpg import clock

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRESSION_FILE = os.path.join(HERE, "data", "progression.json")

DUELS = 40                       # practice duels a spar plays (progression.json sparring.duels)
MAX_DUELS = 200
TIMEOUT = 120                    # seconds before Forge's game is a draw (the duel tool's own limit, as the soak)
HISTORY_KEEP = 40                # records kept in the save, all decks together (the oldest go first)
AGAINST = "this region's foes"
Z = 1.96

TITLE = "Sparring"
STARTING = "Starting Forge for the practice duels (about half a minute)..."
PLAYING = "Practice duel {n} of {total}. Won {wins} so far."
STOPPING = "Stopping..."
STOP = "Stop"
WON_LINE = "Won {wins} of {n} against {against} ({pct}%; likely {lo}-{hi}%)."
NONE_LINE = "No practice duel was finished, so there is nothing to tell yet."
KIND_LINE = "{name}: won {wins} of {n}."
DRAW_LINE = "{n} ended in a draw (Forge's time limit) and count as not won."
STOPPED_LINE = "Stopped after {n} of {total} duels."
ERROR_LINE = "{n} couldn't be played and don't count."
FREE_LINE = "Practice only: no gold, cards or XP, and no time passes."
LEVEL_LINE = "Your deck played with your level and gear: {words}"
LAST_LINE = "Last sparred: Day {day}, {pct}% ({lo}-{hi}%)"
CHANGED = " - before your last changes"
BEFORE_LINE = "Before: {items}"
SHORT_LINE = "Sparred: {pct}% ({lo}-{hi}%)"
NO_TOOL = ("Sparring needs Forge's duel tool (java_rpg/rpg_tools.jar) and Java; this copy has {what}. The yard can't run practice "
           "duels here.")


class SparError(Exception):
    """Sparring can't start; str() is the sentence for the window."""


# ---- settings --------------------------------------------------------------------------------------------------------------------

class Settings:
    def __init__(self, d=None):
        d = d if isinstance(d, dict) else {}
        s = d.get("sparring", {})
        if not isinstance(s, dict):
            raise ValueError("progression.json sparring: a table is needed (Round SPAR1)")
        self.max_duels = _whole(s.get("max_duels", MAX_DUELS), "sparring.max_duels", 1, 10000)
        self.duels = _whole(s.get("duels", DUELS), "sparring.duels", 1, self.max_duels)
        self.timeout = _whole(s.get("timeout", TIMEOUT), "sparring.timeout", 10, 3600)
        ag = s.get("against", {})
        if not isinstance(ag, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in ag.items()):
            raise ValueError("progression.json sparring.against: a table of region -> words is needed")
        self.against = dict(ag)
        unknown = set(s) - {"duels", "max_duels", "timeout", "against"}
        if unknown:
            raise ValueError(f"progression.json sparring: a key it can't use: {sorted(unknown)[0]!r}")

    def against_words(self, region):
        return self.against.get(str(region), AGAINST)


def _whole(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"progression.json {name}: a whole number from {lo} to {hi} is needed, not {v!r}")
    return v


def settings(path=PROGRESSION_FILE):
    with open(path, encoding="utf-8") as f:
        return Settings(json.load(f))


# ---- the foes and the pairings ---------------------------------------------------------------------------------------------------

def region_foes(save, maps_folder=None, enemies_folder=None, world=None):
    """[(kind id, name, source, [decks])] for every kind a REGULAR encounter on this region's maps uses (map property region = the
    save's region), sorted by kind id; each with the deck list the region's regular fights use (rpg.duel.region_source). Kinds
    without a deck file or decks are left out."""
    from rpg import duel, enemies, explore
    folder = maps_folder or explore.MAPS_DIR
    region = save.world.get("region")
    kinds = set()
    for name in explore.map_names(folder):
        area = explore.load_area(name, folder)
        if area.region is not None and region is not None and int(area.region) != int(region):
            continue
        for t in area.triggers:
            if t.kind == "encounter" and t.encounter == "regular" and t.enemy:
                kinds.add(t.enemy)
    source = duel.region_source(save, "regular", world)
    out = []
    for kid in sorted(kinds):
        try:
            data = enemies.read_kind(kid, enemies_folder)
        except (OSError, ValueError):
            continue
        used, decks = enemies.deck_list(data, source)
        if decks:
            out.append((kid, data.get("name", kid), used, decks))
    return out


def deck_cards(deck):
    """A deck's main list ([[name, copies], ...]) as names, copies repeated."""
    out = []
    for n, k in (deck or {}).get("main") or ():
        out += [n] * int(k)
    return out


def plan(save, n, seed, foes=None):
    """The duels: [{"kind", "name", "index", "cards", "seed"}], duel i against kind i mod K; each kind's decks dealt in a shuffled
    order one after another; a Forge seed for each. The same (foes, n, seed) always give the same plan."""
    foes = region_foes(save) if foes is None else foes
    if not foes:
        raise SparError("There are no foes in this region to spar against.")
    rng = random.Random(f"spar:{seed}")
    orders = {}
    for kid, _name, _src, decks in foes:
        o = list(range(len(decks)))
        rng.shuffle(o)
        orders[kid] = o
    used = {kid: 0 for kid, *_r in foes}
    out = []
    for i in range(n):
        kid, name, _src, decks = foes[i % len(foes)]
        k = used[kid]
        used[kid] += 1
        idx = orders[kid][k % len(decks)]
        out.append({"kind": kid, "name": name, "index": idx, "cards": deck_cards(decks[idx]), "seed": rng.randrange(1 << 40)})
    return out


def start_spec(save):
    """The player's start effects in the duel tool's SPEC (start_effects.spec) - "" at level 1 with no perks and no gear."""
    import start_effects
    from rpg import levels
    try:
        eff = levels.duel_effects(save)
    except (OSError, ValueError):
        eff = {}
    return start_effects.spec(eff) if eff else ""


def start_words(save):
    import start_effects
    from rpg import duel, levels
    try:
        return start_effects.describe(levels.duel_effects(save), base_life=duel.LIFE)
    except (OSError, ValueError):
        return ""


def fingerprint(deck):
    """12 hex characters naming the deck's cards (main list, copies counted) - a record says which version of the deck it measured."""
    items = sorted(f"{str(n).lower()}x{int(k)}" for n, k in (deck or {}).get("main") or ())
    return hashlib.sha1("|".join(items).encode("utf-8")).hexdigest()[:12]


# ---- the engine: java_rpg's Duels, one Java start --------------------------------------------------------------------------------

def tool_problem():
    """None when the duel tool can run here, else what is missing (for NO_TOOL)."""
    import paths
    from rpg import rules
    jar = os.path.join(paths.program_dir(), "java_rpg", "rpg_tools.jar")
    if not os.path.isfile(jar):
        return "no java_rpg/rpg_tools.jar"
    if not os.path.isfile(os.path.join(rules.runtime_dir(), "forge.jar")):
        return "no Forge (forge_runtime/forge.jar)"
    import forge_client
    if not forge_client.find_java():
        return "no Java 17 or newer"
    return None


class ForgeEngine:
    """forge.rpgtools.Duels with its pairings on standard input. play() blocks until Forge's line for that pairing arrives (or the
    engine is stopped: then None). kill() ends Java at once, from any thread."""
    name = "forge"

    def __init__(self, folder, timeout=TIMEOUT, memory="2g"):
        import forge_client
        import paths
        from rpg import rules
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        java = forge_client.find_java()
        if not java:
            raise SparError(NO_TOOL.format(what="no Java 17 or newer"))
        rt = rules.runtime_dir()
        jar = os.path.join(paths.program_dir(), "java_rpg", "rpg_tools.jar")
        cp = jar + os.pathsep + os.path.join(rt, "forge.jar")
        self.out = os.path.join(folder, "duels_out.jsonl")
        open(self.out, "w").close()
        self.timeout = timeout
        self.java_log = open(os.path.join(folder, "java.log"), "w", encoding="utf-8")
        cmd = [java, f"-Xmx{memory}", "-Djava.awt.headless=true", "-cp", cp, "forge.rpgtools.Duels", "-", self.out, str(timeout)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)          # no console window on Windows
        self.proc = subprocess.Popen(cmd, cwd=rt, stdin=subprocess.PIPE, stdout=self.java_log, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", creationflags=flags)
        self.n = 0
        self.read_at = 0
        self.decks = {}
        self.killed = False

    def _dck(self, label, cards):
        import forge_client
        key = (label, tuple(sorted(cards)))
        if key not in self.decks:
            path = os.path.join(self.folder, f"{label}_{len(self.decks)}.dck")
            forge_client.write_deck_file(path, [], list(cards), name=label, fmt="standard")
            self.decks[key] = path
        return self.decks[key]

    def play(self, mine, theirs, seed, spec=""):
        self.n += 1
        pid = f"s{self.n}"
        a, b = self._dck("mine", mine), self._dck("foe", theirs)
        if self.proc.poll() is not None:
            return None if self.killed else {"error": f"Java stopped (exit {self.proc.returncode})"}
        cols = [pid, a, b, "1", str(seed)] + ([spec] if spec else [])
        try:
            self.proc.stdin.write("\t".join(cols) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return None if self.killed else {"error": "Java stopped"}
        limit = time.time() + self.timeout + 240            # Forge's own limit, plus the model's start for the first duel
        while time.time() < limit:
            line = self._next(pid)
            if line is not None:
                return line
            if self.proc.poll() is not None:
                return None if self.killed else {"error": f"Java stopped (exit {self.proc.returncode})"}
            time.sleep(0.05)
        return {"error": "no answer from Forge in time"}

    def _next(self, pid):
        with open(self.out, encoding="utf-8") as f:
            f.seek(self.read_at)
            while True:
                pos = f.tell()
                ln = f.readline()
                if not ln or not ln.endswith("\n"):
                    self.read_at = pos
                    return None
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if d.get("id") == pid:
                    self.read_at = f.tell()
                    return d

    def kill(self):
        self.killed = True
        try:
            self.proc.kill()
        except Exception:
            pass

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(20)
        except Exception:
            self.kill()
        try:
            self.java_log.close()
        except Exception:
            pass


def make_engine(folder, timeout=TIMEOUT):
    """The engine a spar uses (tests replace this with a stand-in)."""
    return ForgeEngine(folder, timeout=timeout)


# ---- the run ---------------------------------------------------------------------------------------------------------------------

class Sparring:
    """N practice duels on a background thread. The UI reads progress() each frame and calls stop() for Stop."""

    def __init__(self, save, n=None, seed=None, foes=None, engine_factory=None, timeout=None, against=None):
        st = None
        try:
            st = settings()
        except (OSError, ValueError):
            st = Settings()
        n = st.duels if n is None else n
        if not (isinstance(n, int) and 1 <= n <= st.max_duels):
            raise SparError(f"A spar is 1 to {st.max_duels} duels.")
        deck = save.deck()
        if deck is None:
            raise SparError("You have no deck to spar with.")
        self.deck_name = deck["name"]
        self.deck_key = fingerprint(deck)
        self.mine = deck_cards(deck)
        self.spec = start_spec(save)
        self.words = start_words(save)
        self.seed = seed if seed is not None else random.randrange(1 << 40)
        self.plan = plan(save, n, self.seed, foes)
        self.total = n
        self.against = against or st.against_words(save.world.get("region"))
        self.timeout = timeout or st.timeout
        self.engine_factory = engine_factory or make_engine
        self.games = []                 # finished duels: {"kind", "name", "winner", "turns", "ms"} (errors: {"kind", "error"})
        self.engine = None
        self.error = None               # a sentence when the engine couldn't start
        self.stopped = False
        self.finished = False
        self.started_at = None
        self.ended_at = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._folder = None

    # -- control --
    def start(self):
        self.started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="sparring", daemon=True)
        self._thread.start()
        return self

    def run_here(self):
        """The same run on the caller's thread (tools and tests)."""
        self.started_at = time.monotonic()
        self._run()
        return self

    def stop(self):
        """Stop at once: the duel being played is dropped; every finished one counts."""
        self.stopped = True
        self._stop.set()
        eng = self.engine
        if eng is not None and hasattr(eng, "kill"):
            eng.kill()

    def wait(self, seconds=None):
        if self._thread is not None:
            self._thread.join(seconds)
        return self.finished

    def _run(self):
        try:
            self._folder = tempfile.mkdtemp(prefix="manticore_spar_")
            try:
                self.engine = self.engine_factory(self._folder, timeout=self.timeout)
            except SparError as e:
                self.error = str(e)
                return
            except Exception as e:                       # Java missing, the jar missing, anything: said, never a crash
                self.error = f"The practice duels couldn't start: {type(e).__name__}: {e}"
                return
            if self._stop.is_set():
                return
            for duel in self.plan:
                if self._stop.is_set():
                    break
                res = self.engine.play(self.mine, duel["cards"], duel["seed"], self.spec)
                if res is None or self._stop.is_set():           # stopped while it played: that duel is dropped
                    break
                g = {"kind": duel["kind"], "name": duel["name"], "index": duel["index"]}
                if res.get("error"):
                    g["error"] = str(res["error"])
                else:
                    g.update(winner=res.get("winner"), turns=res.get("turns"), ms=res.get("ms"))
                with self._lock:
                    self.games.append(g)
        finally:
            eng = self.engine
            if eng is not None:
                try:
                    eng.close()
                except Exception:
                    pass
            self.ended_at = time.monotonic()
            self.finished = True
            if self._folder:
                import shutil
                shutil.rmtree(self._folder, ignore_errors=True)

    # -- reading --
    def progress(self):
        """(finished duels, total, wins, started: has the first duel's answer come)."""
        with self._lock:
            games = list(self.games)
        played = [g for g in games if "winner" in g]
        return len(games), self.total, sum(1 for g in played if g["winner"] == "A"), bool(games)

    def result(self):
        """{"deck", "deck_key", "duels", "wins", "draws", "errors", "kinds": {kind: [wins, n, name]}, "total", "stopped",
        "against", "seed", "effects", "seconds", "error"}. duels counts the duels played (draws in, errors out)."""
        with self._lock:
            games = list(self.games)
        played = [g for g in games if "winner" in g]
        kinds = {}
        for kid, name, *_r in _kinds_in(self.plan):
            kinds[kid] = [0, 0, name]
        for g in played:
            k = kinds.setdefault(g["kind"], [0, 0, g["name"]])
            k[1] += 1
            k[0] += g["winner"] == "A"
        secs = None if self.started_at is None else round((self.ended_at or time.monotonic()) - self.started_at, 1)
        return {"deck": self.deck_name, "deck_key": self.deck_key, "duels": len(played),
                "wins": sum(1 for g in played if g["winner"] == "A"),
                "draws": sum(1 for g in played if g["winner"] not in ("A", "B")),
                "errors": len(games) - len(played), "kinds": kinds, "total": self.total,
                "stopped": bool(self.stopped) and len(games) < self.total, "against": self.against, "seed": self.seed,
                "effects": self.spec, "effects_words": self.words, "seconds": secs, "error": self.error,
                "ms": [g.get("ms") for g in played]}


def _kinds_in(plan_):
    seen = []
    for d in plan_:
        if d["kind"] not in [s[0] for s in seen]:
            seen.append((d["kind"], d["name"]))
    return seen


# ---- the words -------------------------------------------------------------------------------------------------------------------

def wilson(w, n, z=Z):
    if n == 0:
        return (0.0, 1.0)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct_range(w, n):
    """(pct, lo, hi) as whole percents."""
    lo, hi = wilson(w, n)
    return round(100 * w / n) if n else 0, round(100 * lo), round(100 * hi)


def result_lines(res):
    if res.get("error"):
        return [res["error"]]
    n, w = res["duels"], res["wins"]
    out = []
    if n:
        p, lo, hi = pct_range(w, n)
        out.append(WON_LINE.format(wins=w, n=n, against=res["against"], pct=p, lo=lo, hi=hi))
        for _kid, (kw, kn, name) in res["kinds"].items():
            if kn:
                out.append(KIND_LINE.format(name=name, wins=kw, n=kn))
    else:
        out.append(NONE_LINE)
    if res.get("draws"):
        out.append(DRAW_LINE.format(n=res["draws"]))
    if res.get("errors"):
        out.append(ERROR_LINE.format(n=res["errors"]))
    if res.get("stopped"):
        out.append(STOPPED_LINE.format(n=n + res.get("errors", 0), total=res["total"]))
    if res.get("effects_words"):
        out.append(LEVEL_LINE.format(words=res["effects_words"]))
    out.append(FREE_LINE)
    return out


# ---- the save's history ----------------------------------------------------------------------------------------------------------

def record(save, res, now=None):
    """The result into the save's "sparring" list (newest last; HISTORY_KEEP at most). Nothing for a spar with no duel played.
    Returns the record or None."""
    if not res or res.get("error") or not res.get("duels"):
        return None
    t = clock.now(save.world) if now is None else now
    rec = {"deck": res["deck"], "cards": res["deck_key"], "day": clock.day_number(t), "duels": res["duels"], "wins": res["wins"],
           "draws": res.get("draws", 0), "kinds": {k: [v[0], v[1]] for k, v in res["kinds"].items() if v[1]},
           "effects": res.get("effects") or "", "at": round(time.time(), 1)}
    hist = list(getattr(save, "sparring", None) or [])
    hist.append(rec)
    save.sparring = hist[-HISTORY_KEEP:]
    return rec


def history(save, deck):
    """The records of this deck (by name), newest first."""
    if deck is None:
        return []
    return [r for r in reversed(getattr(save, "sparring", None) or []) if r.get("deck") == deck.get("name")]


def short_line(save, deck):
    """The deck editor's one line when there is little room: "Sparred: 65% (50-78%)"; "" when never sparred."""
    hist = history(save, deck)
    if not hist:
        return ""
    p, lo, hi = pct_range(hist[0]["wins"], hist[0]["duels"])
    return SHORT_LINE.format(pct=p, lo=lo, hi=hi)


def history_lines(save, deck, before=2):
    """The deck editor's lines: "Last sparred: Day 4, 65% (50-78%)" (+ " - before your last changes" when the deck's cards differ
    from the ones measured), then "Before: Day 2, 55%; Day 1, 48%" for up to `before` older ones. [] when never sparred."""
    hist = history(save, deck)
    if not hist:
        return []
    r = hist[0]
    p, lo, hi = pct_range(r["wins"], r["duels"])
    line = LAST_LINE.format(day=r["day"], pct=p, lo=lo, hi=hi)
    if r.get("cards") and deck is not None and r["cards"] != fingerprint(deck):
        line += CHANGED
    out = [line]
    older = [f"Day {o['day']}, {pct_range(o['wins'], o['duels'])[0]}%" for o in hist[1:1 + before]]
    if older:
        out.append(BEFORE_LINE.format(items="; ".join(older)))
    return out
