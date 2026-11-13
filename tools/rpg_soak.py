# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure soak (Round RPG-SOAK1, Karl 2 Nov 2026: "Is it possible to make a soak test of adventure mode? To hone
economy/progression?"; developer tool - the game never runs it).

Bots play the real Adventure: the real AdventureFlow, headless, walked one key at a time as the slice walk does
(tests/test_slice_walk.py, tests/rpg_route.py - the economy lanes' scripted walk, claude/SLICE_ECONOMY_2026-10-24.md), but every
duel is a REAL Forge game of the bot's CURRENT deck against the deck the game chose for the foe (java_rpg/rpg_tools.jar's
forge.rpgtools.Duels, Forge's own AI on both seats, one Java start kept open for all of a worker's duels), and its result goes
back through the game's own settling code (the table's adventure_settle -> rpg.adventure.finish_duel -> rpg.duel.finish), so gold,
cards, losses, the clock and the foes are the game's own. Between fights the bot shops (by its type) and rebuilds its deck.

    python tools/rpg_soak.py --hours 8                         (Karl's PC: python tools\\rpg_soak.py --hours 8)
    python tools/rpg_soak.py --runs 4 --player shopper --numbers tools/rpg_soak_settings/option_a.json
    python tools/rpg_soak.py --summary rpg_soak_runs/run_<date>_<time>       (summary again from a finished folder)
    python tools/rpg_soak.py --compare rpg_saves/duel_stats [--summary FOLDER]

Player types (--player, several allowed; default all four, taken in turn):
  rusher   the economy lanes' main path: both quests, Pell's three Ember Bandits (nearest first), Pell, the Warrens, the Grave
           Priest, back to Elder Hesk. Buys nothing.
  grinder  fights the nearest foe on the outskirts (any kind) until it has won GRIND_WINS duels in all, walking back to Lantern
           Hollow to bring the beaten foes back when none is left; then the rusher's path. Buys nothing.
  shopper  the rusher's path; at every visit to Lantern Hollow (and so before the next fight) it spends: every single in the shop
           that would go into its deck (on-colour, a higher tier than the deck's weakest card), best tier first, while it can pay
           ("spend before you fight", SLICE_ECONOMY section 4.4).
  gambler  the rusher's path; at every town visit, while it can afford two packs it buys one, then sells its chaff: every card not
           in the deck that can't go into it (off-colour, a land, or no better than the deck's weakest card), keeping one copy.
Every type rebuilds its deck after every win, every purchase and every town visit (rebuild_deck: the rule is in its words).
After the slice's end (Elder Hesk's hand-in) every type goes on as the grinder, still shopping by its type, until --duels
(--after-end stop: the run ends there) - the slice alone is only a few duels when they're won.

Per run: one JSON line per duel and one per run in <out>/records/<settings>__<player>__<seed>.jsonl (gzipped once the run ends);
<out>/summary.txt and summary.json over all runs. Default <out>: rpg_soak_runs/run_<date>_<time> (git-ignored and skipped by
backup.py); runs older than the newest KEEP_RUNS keep only their summaries.
"""
import argparse
import datetime
import glob
import gzip
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLAYERS = ("grinder", "rusher", "shopper", "gambler")
OUT_DIR = os.path.join(BASE, "rpg_soak_runs")      # .gitignore and backup.py TOP_LEVEL_SKIP (tests/test_rpg_soak.py)
KEEP_RUNS = 5                                      # run folders whose records are kept; older ones keep only their summaries
DUEL_CAP = 30                                      # duels a run may take in all (the slice's end, then grinding)
AFTER_END = "grind"                                # after Elder Hesk's hand-in: "grind" on (every type, as the grinder) or "stop"
GRIND_WINS = 8                                     # grinder: wins before it takes the main path [estimate, mine]
STUCK_DUELS = 15                                   # no new place or quest step in this many duels: "stuck"
MINUTES_PER_DUEL = 11                              # 10 min a duel + 1 of screens (SLICE_ECONOMY R-e) [estimate]
FIXED_MINUTES = 6                                  # SLICE_ECONOMY R-e
DUEL_TIMEOUT = 120                                 # seconds before Forge's game is a draw (rpg_balance.TIMEOUT)
DRAW_REPLAYS = 2                                   # a draw (Forge's time limit) is played again with another seed this often
POWER_BANDS = ((0, 1.5, "<1.5"), (1.5, 2.0, "1.5-2.0"), (2.0, 2.5, "2.0-2.5"), (2.5, 9, ">=2.5"))


# ---- the duel engines -------------------------------------------------------------------------------------------------------

class ForgeDuels:
    """forge.rpgtools.Duels with its pairings on standard input ("-"): one Java start (Forge's model, about 25 s) for every duel of
    a worker. play() writes both decks as .dck files, sends one pairing of one game and waits for its line."""
    name = "forge"

    def __init__(self, folder, timeout=DUEL_TIMEOUT, memory="2g", log=None):
        import forge_client
        from rpg import rules
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        java = forge_client.find_java()
        if not java:
            raise SystemExit("No Java 17 or newer found (JAVA_HOME or PATH).")
        rt = rules.runtime_dir()
        jar = os.path.join(BASE, "java_rpg", "rpg_tools.jar")
        cp = jar + os.pathsep + os.path.join(rt, "forge.jar")
        self.out = os.path.join(folder, "duels_out.jsonl")
        open(self.out, "w").close()
        self.timeout = timeout
        self.java_log = open(os.path.join(folder, "java.log"), "w", encoding="utf-8")
        cmd = [java, f"-Xmx{memory}", "-Djava.awt.headless=true", "-cp", cp, "forge.rpgtools.Duels", "-", self.out, str(timeout)]
        self.proc = subprocess.Popen(cmd, cwd=rt, stdin=subprocess.PIPE, stdout=self.java_log, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8")
        self.n = 0
        self.read_at = 0
        self.decks = {}

    def _dck(self, label, cards):
        import forge_client
        key = (label, tuple(sorted(cards)))
        if key not in self.decks:
            path = os.path.join(self.folder, f"{label}_{len(self.decks)}.dck")
            forge_client.write_deck_file(path, [], list(cards), name=label, fmt="standard")
            self.decks[key] = path
        return self.decks[key]

    def play(self, mine, theirs, seed, spec=""):
        """mine, theirs: lists of card names (copies repeated); spec: seat A's start effects (PROG3's SPEC, the 6th column; "" none).
        Returns {"winner": "A" (mine) | "B" | "draw", "turns", "ms"} or {"error": ...}."""
        self.n += 1
        pid = f"d{self.n}"
        a, b = self._dck("bot", mine), self._dck("foe", theirs)
        if self.proc.poll() is not None:
            return {"error": f"Java stopped (exit {self.proc.returncode}); see {self.java_log.name}"}
        cols = [pid, a, b, "1", str(seed)] + ([spec] if spec else [])
        self.proc.stdin.write("\t".join(cols) + "\n")
        self.proc.stdin.flush()
        limit = time.time() + self.timeout + 240          # Forge's own limit, plus the model's start for the first duel
        while time.time() < limit:
            line = self._next(pid)
            if line is not None:
                return line
            if self.proc.poll() is not None:
                return {"error": f"Java stopped (exit {self.proc.returncode}); see {self.java_log.name}"}
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
                d = json.loads(ln)
                if d.get("id") == pid:
                    self.read_at = f.tell()
                    return d

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(20)
        except Exception:
            self.proc.kill()
        self.java_log.close()


class StandInDuels:
    """For the offline tests: decides with a function of (mine, theirs, rng) -> "A" | "B" | "draw"; turns=0 unless asked, so the
    verdict can tell it isn't Forge."""
    name = "stand-in"

    def __init__(self, decide=None, turns=0, seed=1):
        self.rng = random.Random(seed)
        self.decide = decide or (lambda mine, theirs, rng: "A" if rng.random() < 0.6 else "B")
        self.turns = turns
        self.calls = []

    def play(self, mine, theirs, seed, spec=""):
        self.calls.append((list(mine), list(theirs), seed))
        self.specs = getattr(self, "specs", []) + [spec]
        return {"winner": self.decide(mine, theirs, self.rng), "turns": self.turns, "ms": 1}

    def close(self):
        pass


# ---- the deck policy (no AI deck builder: a fixed rule) ----------------------------------------------------------------------

def deck_colours(main, pool):
    """The colours of the deck's non-land cards (letters W U B R G), at most what the deck already plays."""
    cols = set()
    for n, _k in main:
        c = pool.get(n)
        if c is not None and not c.is_land:
            cols |= {x for x in c.colours if x in "WUBRG"}
    return cols


def on_colour(card, cols):
    return card.colours == "C" or all(x in cols for x in card.colours if x in "WUBRG")


def deck_power(main, pool, tier_table):
    """The mean tier of the deck's non-land cards, copies counted (prog1's meter: "Deck power 1.4"). None for no ranked card."""
    tot = k_all = 0
    for n, k in main:
        c = pool.get(n)
        if c is None or c.is_land:
            continue
        t = tier_table.tier_of(c.name)
        if t is None:
            continue
        tot += t * int(k)
        k_all += int(k)
    return round(tot / k_all, 3) if k_all else None


def _rank_weak(card, tier):
    return (tier, -card.mana_value, card.name)          # lowest tier first, then the highest mana value


def _rank_good(card, tier):
    return (-tier, card.mana_value, card.name)          # highest tier first, then the lowest mana value


KIND_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment")
REBUILD_RULES = ("shape", "tier", "none")
MV_SLACK = 1                                       # PROG-TUNE: a swap keeps the mana value within this (Karl, 3 Nov 06:59)


def card_kind(card):
    """The card's one type for the deck rule: Creature for any creature (an artifact creature too), else the first of KIND_ORDER in
    its type line."""
    front = card.type_line.split(" - ")[0]
    for k in KIND_ORDER:
        if k in front.split():
            return k
    return front.split()[-1] if front.split() else "?"


def _fits(rule, out_card, in_card):
    """May in_card replace out_card? tier (RPG-SOAK1's rule): any; shape (PROG-TUNE, Karl 3 Nov 06:59: "swap a card only for one of
    the same card type and a mana value within 1, keeping the creature count and the curve"): the same card_kind and a mana value
    within MV_SLACK."""
    if rule != "shape":
        return True
    return card_kind(out_card) == card_kind(in_card) and abs(out_card.mana_value - in_card.mana_value) <= MV_SLACK


def rebuild_deck(deck, collection, pool, tier_table, lists, rule="shape"):
    """The bot's deck rebuild, in place. Returns [(out, in)] swaps. The rule:
    - the deck's colours are the colours its non-land cards have now (a starting deck has two at most, the RPG9 rule), and stay so;
    - candidates are owned non-land cards, on-colour (or colourless), ranked by the tier table, not banned or restricted, with copies
      the deck doesn't use yet (and at most 4 a name in the deck, 1 for a restricted card - none are candidates);
    - rule "tier" (RPG-SOAK1): repeat: the deck's weakest non-land card (lowest tier, then highest mana value; an unranked card
      counts as tier 0) is swapped for the best candidate (highest tier, then lowest mana value) when that one's tier is higher;
    - rule "shape" (PROG-TUNE, the default): the same, but a card may only be swapped for a candidate of the same card type
      (card_kind) and a mana value within MV_SLACK, so the creature count and the curve stay; the deck's cards are tried weakest
      first, and the first that has a better fitting candidate is swapped;
    - lands and the deck's size never change; a rebuild whose deck fails the RPG's rules or ownership (rpg.collection.problems) is
      undone."""
    from rpg.collection import problems
    if rule == "none":
        return []
    main = [[n, int(k)] for n, k in deck.get("main", [])]
    before = [list(x) for x in main]
    cols = deck_colours(main, pool)
    swaps = []
    for _ in range(80):
        in_deck = {}
        for n, k in main:
            in_deck[n] = in_deck.get(n, 0) + k
        weak = []
        for n, k in main:
            c = pool.get(n)
            if c is None or c.is_land or k <= 0:
                continue
            weak.append((_rank_weak(c, tier_table.tier_of(c.name) or 0), n, c))
        weak.sort()
        if not weak:
            break
        cands = []
        for name in collection.names():
            c = pool.get(name)
            if c is None or c.is_land or c.is_basic or not on_colour(c, cols):
                continue
            if lists.is_banned(c.name) or lists.is_restricted(c.name):
                continue
            t = tier_table.tier_of(c.name)
            if t is None:
                continue
            used = in_deck.get(c.name, 0)
            have = collection.count(c.name) or 0
            if used >= have or used >= 4:
                continue
            cands.append((_rank_good(c, t), c.name, t, c))
        cands.sort()
        pick = None
        for wr, wn, wc in (weak if rule == "shape" else weak[:1]):
            best = next((x for x in cands if _fits(rule, wc, x[3]) and x[1] != wn), None)
            if best is not None and best[2] > wr[0]:
                pick = (wn, best[1])
                break
        if pick is None:
            break
        for row in main:
            if row[0] == pick[0] and row[1] > 0:
                row[1] -= 1
                break
        row = next((r for r in main if r[0] == pick[1]), None)
        if row is None:
            main.append([pick[1], 1])
        else:
            row[1] += 1
        main = [r for r in main if r[1] > 0]
        swaps.append(pick)
    if not swaps:
        return []
    trial = dict(deck, main=main)
    if problems(trial, collection, pool, lists):
        deck["main"] = before
        return []
    deck["main"] = main
    return swaps


def improves(card, deck_main, pool, tier_table, rule="shape"):
    """Would this card go into the deck (on-colour non-land, a tier above a deck card it may replace: the deck's weakest non-land
    card for rule tier, any deck card of the same type and a mana value within MV_SLACK for rule shape)?"""
    if card is None or card.is_land:
        return False
    cols = deck_colours(deck_main, pool)
    if not on_colour(card, cols):
        return False
    t = tier_table.tier_of(card.name)
    if t is None:
        return False
    outs = [pool.get(n) for n, _k in deck_main if pool.get(n) is not None and not pool.get(n).is_land]
    if rule == "shape":
        outs = [c for c in outs if _fits(rule, c, card) and c.name != card.name]
    weakest = min(((tier_table.tier_of(c.name) or 0) for c in outs), default=None)
    return weakest is not None and t > weakest


# ---- shopping ----------------------------------------------------------------------------------------------------------------

PERK_ORDER = ("stout", "quick_draw", "hoard", "thick_skin", "bounty", "keen_eye", "pack_mule", "haggler", "fleet_foot")
GEAR_SCORE = {"life": 1.0, "hand": 3.0, "battlefield": 1.5, "command": 2.0, "reward_choices": 0.5, "gold_mult": 0.0,
              "walk_speed": 0.0}                     # PROG-TUNE: the bot's equip rule - what a piece is worth in a duel [estimate]


def gear_score(piece):
    """A piece's worth to the bot: duel effects only (life 1 a point, a card in hand 3, a permanent at the start 1.5, a relic 2)."""
    e = piece.effect
    v = 0.0
    for k, w in GEAR_SCORE.items():
        if k not in e:
            continue
        x = e[k]
        if isinstance(x, list):
            v += w * len(x)
        elif isinstance(x, bool):
            v += w
        elif k in ("gold_mult", "walk_speed"):
            continue
        else:
            v += w * x
    return v


def progress_step(save):
    """PROG-TUNE: the bot's progression rules, for every type, whenever it can: take every waiting perk (PERK_ORDER, the first
    offered), and wear the best owned piece in each open slot (gear_score; a piece that scores 0 or less is left off). Returns
    {"perks": [ids taken], "worn": [ids put on]}."""
    from rpg import levels, gear
    out = {"perks": [], "worn": []}
    try:
        lv = levels.read()
        for _ in range(10):
            offs = levels.offers(save)
            if not offs:
                break
            grp = offs[0]
            pid = next((x for x in PERK_ORDER if x in grp), grp[0])
            levels.choose(save, pid, lv)
            out["perks"].append(pid)
    except (OSError, ValueError):
        pass
    try:
        gr = gear.read()
        own = [p for p in gear.owned(save, gr) if p is not None and gear_score(p) > 0]
        for slot in gear.open_slots(save, gr):
            worn_now = dict((sl.id, pc) for sl, pc in gear.worn(save, gr))
            fit = [p for p in own if gear.can_wear_in(p, slot)]
            if not fit:
                continue
            best = max(fit, key=lambda p: (gear_score(p), p.id))
            cur = worn_now.get(slot.id)
            if cur is not None and gear_score(cur) >= gear_score(best):
                continue
            if any(pc.id == best.id for pc in worn_now.values()):
                continue
            gear.wear(save, best.id, slot.id, gr)
            out["worn"].append(best.id)
    except (OSError, ValueError, Exception) as e:          # GearError is an Exception
        if type(e).__name__ not in ("GearError", "OSError", "ValueError"):
            raise
    return out


def pack_menu(shop):
    """PROG-TUNE: every pack product the town sells, as (kind, code, price, buy function): plain packs, premium packs, bundles of 3
    and the Jumpstart theme packs on the shelf (Round PACK1)."""
    out = []
    for code, _n, price in shop.packs():
        out.append(("pack", code, price, shop.buy_pack))
    for code, _n, price in shop.premium_packs():
        out.append(("premium", code, price, shop.buy_premium))
    for code, _n, price in shop.bundles():
        out.append(("bundle", code, price, shop.buy_bundle))
    try:
        for half, price in shop.themes():
            out.append(("theme", half.code, price, shop.buy_theme))
    except Exception:
        pass
    return out


def shop_visit(player, save, town_id, world, numbers, pool, tier_table, lists, rebuild=True, rule="shape", game=None,
               visit=0, seed=0):
    """The bot's purchases at a town visit, by type. Returns {"bought": [names], "packs": n, "sold": [(name, k)], "spent", "got",
    "rebuilds", "gear": [ids], "trades": [(out, in)], "bounty_gold", "pack_visits": [...]} . rebuild False (--rebuild none): the
    deck is never touched.
    PROG-TUNE additions: every type has the bounty board's finished jobs paid (it talks to the clerk at every visit); the shopper
    also buys the town's gear (best gear_score per gold first) after its singles; the gambler buys ONE pack kind a visit, taken in
    turn from pack_menu by (seed + visit) - so every kind gets bought across runs - and trades up (4 copies of a card it doesn't
    use for 1 a tier higher) before selling its chaff. Nobody enters the gauntlet (its duels need the warden's screens)."""
    from rpg.shop import Shop, ShopError
    out = {"bought": [], "packs": 0, "sold": [], "spent": 0, "got": 0, "rebuilds": 0, "gear": [], "trades": [],
           "bounty_gold": 0, "pack_visits": []}
    if game is not None:
        try:
            from rpg import extras
            g0 = save.gold
            extras.post_bounties(save)
            out["bounties_paid"] = len(extras.collect_bounties(game))
            out["bounty_gold"] = save.gold - g0
        except (OSError, ValueError):
            pass
    if player not in ("shopper", "gambler"):
        return out
    shop = Shop(save, town_id, world, numbers, pool=pool, tier_table=tier_table, lists=lists)
    deck = save.deck()
    if deck is None:
        return out
    if player == "shopper":
        for _ in range(40):
            offers = [o for o in shop.singles() if o.left > 0 and o.price <= save.gold
                      and improves(pool.get(o.name), deck["main"], pool, tier_table, rule)]
            if not offers:
                break
            o = sorted(offers, key=lambda o: (-o.tier, o.price, o.name))[0]
            try:
                shop.buy_single(o.slot)
            except ShopError:
                break
            out["bought"].append(o.name)
            out["spent"] += o.price
            if rebuild and rebuild_deck(deck, save.collection, pool, tier_table, lists, rule):
                out["rebuilds"] += 1
        try:
            from rpg import gear
            gr = gear.read()
            region = save.world.get("region") or 1
            for _ in range(10):
                offs = [p for p in gear.shop_offers(save, region, gr) if p.price <= save.gold and gear_score(p) > 0]
                if not offs:
                    break
                p = max(offs, key=lambda p: (gear_score(p) / max(1, p.price), -p.price, p.id))
                gear.buy(save, p.id, region, gr)
                out["gear"].append(p.id)
                out["spent"] += p.price
        except (OSError, ValueError) as e:
            pass
        except Exception as e:
            if type(e).__name__ != "GearError":
                raise
    else:
        menu = pack_menu(shop)
        g_before = save.gold
        pv = None
        if menu:
            kind, code, price, buy = menu[(seed + visit) % len(menu)]
            pv = {"kind": kind, "code": code, "price": price, "n": 0, "gold_before": g_before}
            while save.gold >= 2 * price:
                try:
                    buy(code)
                except ShopError:
                    break
                out["packs"] += 1
                pv["n"] += 1
                out["spent"] += price
        if rebuild and rebuild_deck(deck, save.collection, pool, tier_table, lists, rule):
            out["rebuilds"] += 1
        from rpg.collection import deck_counts
        from rpg.cardpool import normal_name
        used = {k: v for k, (_n, v) in deck_counts(deck).items()}
        if game is not None:
            try:
                from rpg import extras
                ex = extras.read()
                for _ in range(20):
                    rows = [r for r in extras.trade_rows(save, tier_table, shop.band, used, ex) if r[3] == 0]
                    if not rows:
                        break
                    name = sorted(rows, key=lambda r: (r[1], r[0]))[0][0]
                    got, _t, _taken = extras.trade_up(save, name, pool, tier_table, lists, numbers, shop.band, ex)
                    out["trades"].append((name, got))
            except (OSError, ValueError):
                pass
            except Exception as e:
                if type(e).__name__ != "TradeError":
                    raise
            if out["trades"] and rebuild and rebuild_deck(deck, save.collection, pool, tier_table, lists, rule):
                out["rebuilds"] += 1
                used = {k: v for k, (_n, v) in deck_counts(deck).items()}
        for name in sorted(save.collection.names()):
            c = pool.get(name)
            if c is None or c.is_basic or save.collection.is_free(name):
                continue
            have = save.collection.count(name) or 0
            in_use = used.get(normal_name(name), 0)
            if in_use:
                continue
            if not (c.is_land or not improves(c, deck["main"], pool, tier_table, rule)):
                continue
            k = have - 1                                   # keep one copy (the Card Book's "ever owned" stays honest)
            if k < 1 or shop.sell_price(name) < 1:
                continue
            try:
                got = shop.sell(name, k)
            except ShopError:
                continue
            out["sold"].append((name, k))
            out["got"] += got
        if pv is not None:
            pv["gold_after"] = save.gold
            pv["flip"] = save.gold > g_before and pv["n"] > 0
            out["pack_visits"].append(pv)
    return out


# ---- records, summary, verdict ----------------------------------------------------------------------------------------------

def band_of(power):
    if power is None:
        return "?"
    for lo, hi, label in POWER_BANDS:
        if lo <= power < hi:
            return label
    return "?"


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(math.ceil(q * len(xs))) - 1))
    return xs[i]


def wilson(w, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def read_records(folder):
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "records", "*.jsonl*"))):
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", encoding="utf-8") as f:
            out += [json.loads(ln) for ln in f if ln.strip()]
    return out


def summarize(records, expect_forge=True):
    """{(settings, player): {...}} plus a verdict. records: the duel and run lines."""
    runs = [r for r in records if r.get("t") == "run"]
    duels = [r for r in records if r.get("t") == "duel"]
    groups = {}
    for r in runs:
        groups.setdefault((r["settings"], r["player"]), {"runs": [], "duels": []})["runs"].append(r)
    for d in duels:
        groups.setdefault((d["settings"], d["player"]), {"runs": [], "duels": []})["duels"].append(d)
    out = {}
    for (s, p), g in sorted(groups.items()):
        rr, dd = g["runs"], g["duels"]
        foes = {}
        for d in dd:
            if d.get("result") not in ("won", "lost"):
                continue
            k = (d.get("kind"), d.get("enc"), band_of(d.get("power")))
            w, n = foes.get(k, (0, 0))
            foes[k] = (w + (d["result"] == "won"), n + 1)
        stat = lambda key: {"median": pct([r.get(key) for r in rr], 0.5), "p90": pct([r.get(key) for r in rr], 0.9)}
        out[f"{s}|{p}"] = {
            "settings": s, "player": p, "runs": len(rr), "duels": len(dd),
            "done": sum(1 for r in rr if r.get("done")), "stuck": sum(1 for r in rr if r.get("stuck")),
            "loops": sum(1 for r in rr if r.get("loop")), "errors": sum(1 for r in rr if r.get("error")),
            "duels_n": stat("duels_n"), "duels_to_boss": stat("boss_at"), "minutes_to_boss": stat("boss_minutes"),
            "duels_to_end": stat("end_at"), "minutes_to_end": stat("end_minutes"),
            "gold_end": stat("gold_end"), "gold_won": stat("gold_won"), "gold_lost": stat("gold_lost"),
            "gold_spent": stat("gold_spent"), "power_end": stat("power_end"), "power_gain": stat("power_gain"),
            "bought": sum(len(r.get("bought", [])) for r in rr), "packs": sum(r.get("packs", 0) for r in rr),
            "rebuilds": sum(r.get("rebuilds", 0) for r in rr),
            "tiers_end": {t: stat(f"tier{t}") for t in range(1, 6)},
            "win_rate": {f"{k[0]}/{k[1]}/{k[2]}": {"won": w, "of": n, "rate": round(w / n, 3) if n else None,
                                                   "low": round(wilson(w, n)[0], 3), "high": round(wilson(w, n)[1], 3)}
                         for k, (w, n) in sorted(foes.items(), key=lambda kv: str(kv[0]))},
        }
    return {"groups": out, "verdict": verdict(runs, duels, expect_forge)}


def verdict(runs, duels, expect_forge=True):
    """("VALID" | "INVALID", [reasons])."""
    why = []
    if not duels:
        why.append("no duel was played")
    real = [d for d in duels if d.get("engine") == "forge" and (d.get("turns") or 0) > 0]
    if expect_forge and duels and len(real) < 0.9 * len(duels):
        why.append(f"only {len(real)} of {len(duels)} duels were real Forge games with turns (a coin or a stand-in decided "
                   "the rest)")
    errs = [d for d in duels if d.get("error")]
    if duels and len(errs) > 0.05 * len(duels):
        why.append(f"{len(errs)} of {len(duels)} duels failed to run")
    shoppers = [r for r in runs if r.get("player") == "shopper"]
    if shoppers and not any(r.get("bought") for r in shoppers):
        why.append("no shopper ever bought a card")
    tiered = [r for r in runs if r.get("rebuild_policy", "tier") in ("tier", "shape")]
    if tiered and not any(r.get("rebuilds") for r in tiered) and any(r.get("wins") for r in tiered):
        why.append("no deck was ever rebuilt")
    bad_loss = [d for d in duels if d.get("result") == "lost"
                and int(max(0, d.get("gold_before") or 0) * d.get("loss_share", 0.2)) >= 1 and (d.get("gold_change") or 0) >= 0]
    if bad_loss:
        why.append(f"{len(bad_loss)} lost duel(s) took no gold although the loss share of the gold carried was 1 or more")
    bad_win = [d for d in duels if d.get("result") == "won" and (d.get("gold_change") or 0) <= 0]
    if bad_win:
        why.append(f"{len(bad_win)} won duel(s) paid no gold")
    return ("INVALID" if why else "VALID", why)


def summary_text(summ, header=""):
    lines = [header] if header else []
    v, why = summ["verdict"]
    lines.append(f"VERDICT: {v}" + ("" if not why else " - " + "; ".join(why)))
    for key, g in summ["groups"].items():
        f = lambda s: f"{s['median']} (90th {s['p90']})"
        lines += ["", f"== {g['settings']} / {g['player']}: {g['runs']} runs, {g['duels']} duels; done {g['done']}, "
                      f"stuck {g['stuck']}, loops {g['loops']}, errors {g['errors']}",
                  f"  duels a run {f(g['duels_n'])}; duels until the boss is beaten {f(g['duels_to_boss'])} = "
                  f"{f(g['minutes_to_boss'])} min; until the slice's end (Hesk) {f(g['duels_to_end'])} = "
                  f"{f(g['minutes_to_end'])} min ({MINUTES_PER_DUEL} min a duel + {FIXED_MINUTES})",
                  f"  gold at the end {f(g['gold_end'])}; won {f(g['gold_won'])}; lost {f(g['gold_lost'])}; spent "
                  f"{f(g['gold_spent'])}",
                  f"  deck power at the end {f(g['power_end'])}; gain {f(g['power_gain'])}; rebuilds {g['rebuilds']}; "
                  f"singles bought {g['bought']}; packs {g['packs']}",
                  "  cards owned at the end by tier (median): " + ", ".join(
                      f"t{t} {g['tiers_end'][t]['median']}" for t in range(1, 6)),
                  "  win rate by foe / fight / deck-power band:"]
        for k, w in g["win_rate"].items():
            rate = "-" if w["rate"] is None else f"{100 * w['rate']:.0f}%"
            lines.append(f"    {k}: {rate} ({w['won']} of {w['of']}; 95% {100 * w['low']:.0f}-{100 * w['high']:.0f}%)")
    return "\n".join(lines) + "\n"


def compare_text(summ, stats_folder):
    """The bot's win rate per foe kind next to the player's own Adventure duels (rpg_saves/duel_stats)."""
    import stats as stats_mod
    games = stats_mod.load(stats_folder)
    real = {}
    for g in games:
        st = g.get("start") or {}
        en = g.get("end") or {}
        opp = (st.get("opponents") or [{}])[0]
        did = ((opp.get("deck") or {}).get("id") or "")
        kind = did.split(":", 1)[1] if did.startswith("rpg-enemy:") else None
        if kind is None or en.get("result") not in ("won", "lost", "conceded"):
            continue
        w, n = real.get(kind, (0, 0))
        real[kind] = (w + (en["result"] == "won"), n + 1)
    bot = {}
    for g in summ["groups"].values():
        for k, w in g["win_rate"].items():
            kind = k.split("/")[0]
            a, b = bot.get(kind, (0, 0))
            bot[kind] = (a + w["won"], b + w["of"])
    total = sum(n for _w, n in real.values())
    lines = [f"Your own Adventure duels in {stats_folder}: {total} decided game(s)" +
             (" - far too few to calibrate anything; every row below is a sample of the bot only." if total < 30 else ".")]
    for kind in sorted(set(real) | set(bot)):
        a, b = bot.get(kind, (0, 0))
        w, n = real.get(kind, (0, 0))
        lines.append(f"  {kind}: bot {a}/{b}" + (f" = {100 * a / b:.0f}%" if b else "") + f"; you {w}/{n}" +
                     (f" = {100 * w / n:.0f}%" if n else ""))
    return "\n".join(lines) + "\n"


# ---- one run of the real game -----------------------------------------------------------------------------------------------

def make_bot_class():
    """The bot is a subclass of the slice walk's test case (its harness: a headless ForgeTable, a temporary saves folder, the
    walking helpers). Imported late: it needs pygame and forge_runtime/."""
    os.environ.setdefault("MANTICORE_PORTABLE", "1")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    from unittest import mock
    from tests import test_slice_walk as sw
    from tests import test_rpg8 as r8
    from tests.test_forge_table import frame, key
    import rpg_screens as rs
    from rpg import clock, newgame, talk, economy, tiers, cardpool, rules
    from rpg import duel as rduel, levels as rlevels, gear as rgear
    import start_effects
    # PROG-TUNE: the walker keeps gear-free speed. The harness lines the walker up on each tile's middle, and a walker x1.35
    # (Wayfarer Boots) steps past the middle and back for ever (seen at Lantern Hollow's gate). Speed changes nothing the soak
    # measures (minutes are 11 a duel), so gear's walk_speed is held at 1.0 here; every other effect of the gear is the game's.
    rgear.walk_speed = lambda save, gr=None: 1.0

    TOWN, OUT, W1, W2 = sw.TOWN, sw.OUT, sw.W1, sw.W2

    class Bot(sw.SliceWalkTests):
        rebuild_policy = "shape"
        ignore_effects = False

        def runTest(self):
            pass

        def exit_tile(self, a, to_map):
            t = next(t for t in a.triggers if t.kind == "exit" and t.to_map == to_map)
            return a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)

        # -- the duel: Forge decides, the game settles ---------------------------------------------------------------------
        def fight(self, g):
            f = g.modal
            frame(g)
            calls = len(self.launcher.calls)
            self.press(g, "fight")
            if len(self.launcher.calls) != calls + 1:
                why = getattr(g.modal, "result", None)
                self.problems.append(f"Duel started no game: {why}")
                raise RuntimeError(f"Duel started no game: {why}")
            call = self.launcher.calls[-1]
            mine, theirs = list(call["mine"][1]), list(call["opponents"][0][1])
            sv = f.game.save
            pd = dict(sv.pending_duel or {})
            gold0, clk, power = sv.gold, clock.now(sv.world), deck_power(sv.deck()["main"], self.pool, self.tiers)
            effects = call.get("effects") or None
            spec = start_effects.spec(effects) if effects else ""
            if getattr(self, "ignore_effects", False):          # planted error (tests): the duel tool never gets them
                spec = ""
            held0 = rduel.held_by(sv, pd.get("spot")) or 0
            share0 = rlevels.loss_share(sv, self.numbers, pd.get("encounter") or "regular")   # GOLD1 + Thick Skin
            xp0, fw0 = rlevels.state(sv)["xp"], sv.world.get("first_win_day")
            level0 = rlevels.level(sv)
            worn0 = [p.id for _s, p in rgear.worn(sv)]
            res = None
            seeds = []
            for i in range(1 + DRAW_REPLAYS):
                seed = self.rng.randrange(1 << 40)
                seeds.append(seed)
                res = self.engine.play(mine, theirs, seed, spec)
                if res.get("winner") in ("A", "B") or res.get("error"):
                    break
            won = res.get("winner") == "A"
            g.vs = None
            frame(g, 2)
            r8.end_game(g, won=won)
            g.end_continue()
            frame(g)
            f = g.modal
            if not isinstance(f, rs.AdventureFlow):
                raise RuntimeError("the table didn't come back to the Adventure")
            sv = f.game.save
            rec = {"t": "duel", "run": self.run_id, "player": self.player, "settings": self.settings, "n": len(self.duels) + 1,
                   "kind": pd.get("kind"), "enc": pd.get("encounter"), "foe_deck": pd.get("deck"), "source": pd.get("source"),
                   "result": "won" if won else "lost", "forge_result": res.get("winner"), "turns": res.get("turns"),
                   "ms": res.get("ms"), "error": res.get("error"), "engine": self.engine.name, "seeds": seeds,
                   "gold_before": gold0, "gold_after": sv.gold, "gold_change": sv.gold - gold0, "map": pd.get("map"),
                   "clock": clk, "power": power, "cards": sv.collection.total(),
                   "loss_share": 0.0 if pd.get("gauntlet") else share0,
                   "spec": spec, "level": level0, "deck_key": __import__("hashlib").sha1("|".join(sorted(mine)).encode()).hexdigest()[:12], "xp_before": xp0, "xp_after": rlevels.state(sv)["xp"],
                   "level_after": rlevels.level(sv), "worn": worn0, "perks": list(rlevels.state(sv)["perks"]),
                   "held_back": held0 if won else 0, "first_win": won and sv.world.get("first_win_day") != fw0}
            self.duels.append(rec)
            self.emit(rec)
            if won:
                self.gold_won += max(0, sv.gold - gold0)
            else:
                self.gold_lost += max(0, gold0 - sv.gold)
            self.choose_picks(g.modal)
            key(g, pygame.K_RETURN)
            for _ in range(6):
                if g.modal.sub == "talk":
                    self.say_all(g, answer=True)
                elif g.modal.sub == "result":
                    self.choose_picks(g.modal)
                    key(g, pygame.K_RETURN)
                else:
                    break
            self.progress(g.modal)
            if won:
                self.rebuild(g.modal)
            return won

        def choose_picks(self, f):
            """PROG2's pick 1 of 3: in each group the card that would go into the deck by the deck rule (improves), the highest
            tier first; else the highest-tier on-colour card; else the first."""
            res = getattr(f, "results", None)
            if res is None or not getattr(res, "offers", None):
                return
            sv = f.game.save
            deck = sv.deck()
            main = deck["main"] if deck else []
            cols = deck_colours(main, self.pool)
            for gi, grp in enumerate(res.offers):
                best, bkey = 0, None
                for i, c in enumerate(grp):
                    card = self.pool.get(c["name"])
                    if card is None:
                        continue
                    t = self.tiers.tier_of(card.name) or 0
                    k = (improves(card, main, self.pool, self.tiers, self.rebuild_policy), on_colour(card, cols), t, -i)
                    if bkey is None or k > bkey:
                        best, bkey = i, k
                res.picks[gi] = best
            self.picks_made += len(res.offers)

        def progress(self, f):
            got = progress_step(f.game.save)
            self.perks_taken += got["perks"]
            self.gear_worn += got["worn"]
            if got["perks"] or got["worn"]:
                f.game.write()

        def rebuild(self, f):
            if getattr(self, "rebuild_policy", "shape") == "none":
                return
            sv = f.game.save
            deck = sv.deck()
            if deck is None:
                return
            swaps = rebuild_deck(deck, sv.collection, self.pool, self.tiers, self.lists, self.rebuild_policy)
            if swaps:
                self.rebuilds += 1
                self.swaps += swaps
                f.game.write()

        def town_visit(self, f):
            sv = f.game.save
            town = sv.world.get("last_town") or self.world.start_town
            g0 = sv.gold
            self.visits += 1
            got = shop_visit(self.player, sv, town, self.world, self.numbers, self.pool, self.tiers, self.lists,
                             rebuild=getattr(self, "rebuild_policy", "shape") != "none", rule=self.rebuild_policy,
                             game=f.game, visit=self.visits, seed=self.seed)
            self.gear_bought += got["gear"]
            self.trades += got["trades"]
            self.bounty_gold += got["bounty_gold"]
            self.pack_visits += got["pack_visits"]
            self.progress(f)
            self.rebuilds += got["rebuilds"]
            self.bought += got["bought"]
            self.packs += got["packs"]
            self.sold += got["sold"]
            self.spent += got["spent"]
            self.got_sold += got["got"]
            if got["packs"] and sv.gold > g0 + got["bounty_gold"]:
                self.loop_gain += sv.gold - g0 - got["bounty_gold"]
            self.rebuild(f)
            f.game.write()

        def handle(self, g):
            f = g.modal
            if f.stage == "town":
                frame(g); self.press(g, "leave_town"); return "town"
            if f.sub == "fight":
                self.fight(g); return "fight"
            if f.sub == "talk":
                self.say_all(g, answer=True); return "talk"
            if f.sub == "result":
                self.choose_picks(f); key(g, pygame.K_RETURN); return "result"
            if f.sub is not None:
                raise RuntimeError(f"unexpected window {f.sub}")
            return None

        def nearest_foe(self, ex, kinds=None):
            left = [fo for fo in ex.encounters() if kinds is None or fo.enemy in kinds]
            if not left:
                return None
            foe = min(left, key=lambda fo: math.hypot(fo.x - ex.walker.x, fo.y - ex.walker.y))
            spot, a = foe.spot, ex.area

            def where():
                fo = next((x for x in ex.encounters() if x.spot == spot), None)
                return a.tile_at(fo.x, fo.y) if fo else a.tile_at(ex.walker.x, ex.walker.y)
            return where

        def goal(self, g):
            f = g.modal
            w = f.game.save.world
            a = f.explorer.area
            m = a.name
            debt = talk.quest_state(w, "lamplighters_debt")
            boss = talk.BOSS_FLAG in talk.flags(w)
            up = {W2: W1, W1: OUT, OUT: TOWN}
            grinding = self.player == "grinder" and sum(1 for d in self.duels if d["result"] == "won") < GRIND_WINS and not boss
            if grinding or self.slice_done:
                if m == OUT:
                    where = self.nearest_foe(f.explorer)
                    if where is not None:
                        return ("tile", where)
                    return ("tile", self.exit_tile(a, TOWN))
                if m == TOWN:
                    return ("tile", self.exit_tile(a, OUT))
                return ("tile", self.exit_tile(a, up[m]))
            if debt == "active":
                if m == TOWN:
                    return ("tile", self.exit_tile(a, OUT))
                if m == OUT:
                    where = self.nearest_foe(f.explorer, {"ember_bandit"})
                    return ("tile", where if where is not None else self.exit_tile(a, TOWN))
                return ("tile", self.exit_tile(a, up[m]))
            if debt == "ready":
                if m == TOWN:
                    return ("talk", "lamplighter")
                return ("tile", self.exit_tile(a, up[m]))
            if not boss:
                down = {TOWN: OUT, OUT: W1, W1: W2}
                if m in down:
                    return ("tile", self.exit_tile(a, down[m]))
                bt = next(t for t in a.triggers if t.kind == "encounter" and t.encounter == "boss")
                return ("tile", a.tile_at((bt.box[0] + bt.box[2]) / 2, (bt.box[1] + bt.box[3]) / 2))
            if m == TOWN:
                return ("talk", "elder")
            if any(t.kind == "exit" and t.to_map == TOWN for t in a.triggers):
                return ("tile", self.exit_tile(a, TOWN))
            return ("tile", self.exit_tile(a, up[m]))

        def go(self, g, target, limit=20000):
            start = g.modal.explorer.area.name
            last, still, nudges = None, 0, 0
            for _ in range(limit):
                f = g.modal
                if f.stage != "map" or f.sub is not None:
                    self.handle(g); return
                if f.explorer.area.name != start:
                    return
                ex = f.explorer
                here = (ex.walker.x, ex.walker.y)
                if here == last:
                    still += 1
                else:
                    last, still = here, 0
                if still >= 90:                      # walled off (a person out now, a foe): route round what blocks
                    a = ex.area
                    self.avoid[a.name] = set(self.avoid.get(a.name, set())) | set(ex.walker.blockers or ())
                    self.routes = {k: v for k, v in self.routes.items() if k[0] != a.name}
                    still = 0
                    nudges += 1
                    if nudges > 20:
                        break
                press = self.press_for(ex, target)
                if press is None:
                    return "arrived"
                self.tick(g, press)
            f = g.modal
            tgt = target() if callable(target) else target
            raise RuntimeError(f"walk limit on {start} to {tgt} (at {f.explorer.area.tile_at(f.explorer.walker.x, f.explorer.walker.y)}, "
                               f"{len(self.duels)} duels, clock {clock.clock_text(clock.now(f.game.save.world))})")

        def run_one(self, seed, player, settings, run_id, engine, emit, numbers_file=None):
            self.rng = random.Random(seed * 7919 + 1)
            self.player, self.settings, self.run_id, self.engine, self.emit = player, settings, run_id, engine, emit
            self.duels, self.problems, self.swaps = [], [], []
            self.slice_done = False
            self.after_end = getattr(self, "after_end", AFTER_END)
            self.bought, self.sold = [], []
            self.packs = self.rebuilds = self.spent = self.got_sold = self.loop_gain = 0
            self.seed, self.visits, self.bounty_gold, self.picks_made = seed, 0, 0, 0
            self.perks_taken, self.gear_worn, self.gear_bought, self.trades, self.pack_visits = [], [], [], [], []
            self.gold_won = self.gold_lost = 0
            self.pool = cardpool.load()
            self.lists = rules.vintage_lists()
            self.tiers = tiers.load(pool=self.pool)
            self.numbers = economy.read_numbers()
            self.world = economy.read_world()
            g = self.gui(launcher=self.launcher)

            class Seeded(newgame.NewGameFlow):
                def __init__(s, *a, **k):
                    k.setdefault("seed", seed)
                    super().__init__(*a, **k)
            f = self.open(g)
            with mock.patch.object(newgame, "NewGameFlow", Seeded):
                self.press(g, "new:1")
            for ch in "Bot":
                key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
            key(g, pygame.K_RETURN)
            for _ in range(newgame.PICKS):
                offer = f.flow.offer()
                code = next((h for h in offer if h.size == 20), offer[0]).code
                sw.click(g, next(r for r, k, d in f.rows if k == "pick" and d == code).center)
            self.press(g, "begin"); self.press(g, "ok"); self.press(g, "leave_town")
            sv = f.game.save
            gold0 = sv.gold
            power0 = deck_power(sv.deck()["main"], self.pool, self.tiers)
            for nid in ("elder", "lamplighter", "innkeeper"):
                self.talk_to(g, nid, answer=(nid != "innkeeper"), then=None)
            marks, done, steps, last_map = {}, False, 0, None
            progress_at, stuck = 0, False
            seen = set()
            error = None
            try:
                while len(self.duels) < self.cap and steps < 4000:
                    steps += 1
                    f = g.modal
                    if f.stage != "map" or f.sub is not None:
                        self.handle(g); continue
                    w = f.game.save.world
                    m = f.explorer.area.name
                    state = (m, talk.quest_state(w, "lamplighters_debt"), talk.quest_state(w, "the_dark_hall"),
                             talk.BOSS_FLAG in talk.flags(w))
                    if state not in seen:
                        seen.add(state)
                        progress_at = len(self.duels)
                    if not done and len(self.duels) - progress_at >= STUCK_DUELS:
                        stuck = True
                    if m == TOWN and last_map != TOWN:
                        self.town_visit(f)
                    last_map = m
                    if talk.BOSS_FLAG in talk.flags(w) and "boss" not in marks:
                        marks["boss"] = len(self.duels)
                    if not done and talk.quest_state(w, "the_dark_hall") == "done":
                        done = self.slice_done = True
                        marks["end"] = len(self.duels)
                        if self.after_end == "stop":
                            break
                    what, arg = self.goal(g)
                    if what == "talk":
                        if g.modal.sub is None:
                            self.talk_to(g, arg, answer=True, then=None)
                        continue
                    self.go(g, arg)
            except Exception as e:
                import traceback
                error = f"{type(e).__name__}: {e}"
                self.problems.append(traceback.format_exc()[-1500:])
            sv = g.modal.game.save if isinstance(g.modal, rs.AdventureFlow) else sv
            power1 = deck_power(sv.deck()["main"], self.pool, self.tiers)
            by_tier = {t: 0 for t in range(1, 6)}
            for n in sv.collection.names():
                t = self.tiers.tier_of(n)
                if t in by_tier:
                    by_tier[t] += sv.collection.count(n) or 0
            boss_at = marks.get("boss")
            rec = {"t": "run", "run": run_id, "player": player, "settings": settings, "seed": seed, "done": done,
                   "duels_n": len(self.duels), "wins": sum(1 for d in self.duels if d["result"] == "won"),
                   "boss_at": boss_at, "end_at": marks.get("end"),
                   "end_minutes": None if marks.get("end") is None else FIXED_MINUTES + MINUTES_PER_DUEL * marks["end"],
                   "boss_minutes": None if boss_at is None else FIXED_MINUTES + MINUTES_PER_DUEL * boss_at,
                   "minutes_est": FIXED_MINUTES + MINUTES_PER_DUEL * len(self.duels),
                   "gold_start": gold0, "gold_end": sv.gold, "gold_won": self.gold_won, "gold_lost": self.gold_lost,
                   "gold_spent": self.spent, "gold_sold": self.got_sold, "bought": self.bought, "packs": self.packs,
                   "sold": self.sold, "rebuilds": self.rebuilds, "rebuild_policy": getattr(self, "rebuild_policy", "shape"), "swaps": self.swaps[:200],
                   "power_start": power0, "power_end": power1,
                   "power_gain": None if power0 is None or power1 is None else round(power1 - power0, 3),
                   **{f"tier{t}": by_tier[t] for t in by_tier}, "cards_end": sv.collection.total(),
                   "clock_end": clock.now(sv.world), "stuck": stuck, "loop": self.loop_gain > 0, "loop_gain": self.loop_gain,
                   "frames": self.frames, "tiles": round(self.walked, 1), "error": error,
                   "problems": self.problems[-3:], "deck_end": sv.deck()["main"],
                   "level_end": rlevels.level(sv), "xp_end": rlevels.state(sv)["xp"], "perks": self.perks_taken,
                   "gear_worn": [p.id for _s, p in rgear.worn(sv)], "gear_bought": self.gear_bought, "gear_owned":
                   [p.id for p in rgear.owned(sv)], "trades": self.trades, "bounty_gold": self.bounty_gold, "pack_visits": self.pack_visits, "picks_made": self.picks_made,
                   "level_at": {str(L): next((d["n"] for d in self.duels if (d.get("level_after") or 1) >= L), None)
                                for L in (3, 5, 10)}}
            self.emit(rec)
            return rec

    return Bot


def load_numbers_override(path):
    """--numbers FILE: rpg.economy.read_numbers reads this file instead of rpg/data/numbers.json, for this process only."""
    from rpg import economy
    if not path:
        return
    original = economy.read_numbers
    economy.read_numbers = lambda p=None, _o=original, _f=path: _o(_f)
    import rpg.adventure as adv
    if hasattr(adv, "economy"):
        adv.economy.read_numbers = economy.read_numbers


_PROG_READ = {}


def apply_progression(path):
    """--progression FILE / a settings folder's progression.json: rpg.levels, rpg.extras and rpg.progress read this file instead of
    rpg/data/progression.json, for this process (None: the shipped file again)."""
    from rpg import levels, extras, progress
    for mod in (levels, extras, progress):
        orig = _PROG_READ.setdefault(mod.__name__, mod.read)
        if path:
            mod.read = lambda p=None, _o=orig, _f=path: _o(p or _f)
        else:
            mod.read = orig


def load_progression(path):
    """A sentence for the summary's header."""
    if not path:
        return "progression: shipped"
    if not os.path.exists(path):
        raise SystemExit(f"--progression {path}: no such file")
    return f"progression: {path}"


def settings_dirs(paths):
    """--setting DIR (several): [(name, numbers.json or None, progression.json or None)] - a folder under tools/rpg_soak_settings/
    with either file or both (a missing one is the shipped file)."""
    out = []
    for d in paths or []:
        d = os.path.abspath(d)
        if not os.path.isdir(d):
            raise SystemExit(f"--setting {d}: not a folder")
        nf, pf = os.path.join(d, "numbers.json"), os.path.join(d, "progression.json")
        out.append((os.path.basename(d.rstrip(os.sep)), nf if os.path.exists(nf) else None, pf if os.path.exists(pf) else None))
    return out


def child(args):
    """One worker: runs its share of (settings, player, seed) jobs with one Java start."""
    with open(args.jobs, encoding="utf-8") as f:
        jobs = json.load(f)
    folder = args.out
    os.makedirs(os.path.join(folder, "records"), exist_ok=True)
    engine = ForgeDuels(os.path.join(folder, f"java_{args.worker}"), timeout=args.timeout) if not args.stand_in else \
        StandInDuels(seed=args.worker)
    Bot = make_bot_class()
    end_at = time.time() + args.seconds if args.seconds else None
    try:
        for job in jobs:
            settings, numbers_file, player, seed = job[:4]
            prog_file = job[4] if len(job) > 4 else None
            if end_at and time.time() > end_at:
                break
            importlib_reset_numbers(numbers_file)
            apply_progression(prog_file)
            name = f"{settings}__{player}__{seed}"
            path = os.path.join(folder, "records", name + ".jsonl")
            with open(path, "w", encoding="utf-8") as fo:
                def emit(rec, _fo=fo):
                    _fo.write(json.dumps(rec) + "\n"); _fo.flush()
                bot = Bot()
                bot.cap = args.duels
                bot.after_end = args.after_end
                bot.rebuild_policy = args.rebuild
                bot.ignore_effects = args.ignore_effects
                bot.setUp()
                t0 = time.time()
                try:
                    r = bot.run_one(seed, player, settings, name, engine, emit)
                    print(f"[{args.worker}] {name}: done={r['done']} duels={r['duels_n']} gold={r['gold_end']} "
                          f"power {r['power_start']}->{r['power_end']} {time.time() - t0:.0f}s", flush=True)
                finally:
                    try:
                        bot.tearDown()
                    except Exception:
                        pass
                    from rpg import explore
                    explore.forget_areas()
            with open(path, "rb") as fi, gzip.open(path + ".gz", "wb") as fz:
                shutil.copyfileobj(fi, fz)
            os.remove(path)
    finally:
        engine.close()


_ORIGINAL_READ = None


def importlib_reset_numbers(numbers_file):
    global _ORIGINAL_READ
    from rpg import economy
    if _ORIGINAL_READ is None:
        _ORIGINAL_READ = economy.read_numbers
    if numbers_file:
        economy.read_numbers = lambda p=None, _f=numbers_file: _ORIGINAL_READ(_f)
    else:
        economy.read_numbers = _ORIGINAL_READ


def prune(root, keep=KEEP_RUNS):
    """Runs older than the newest `keep` lose their records and Java folders; summaries stay."""
    runs = sorted(d for d in glob.glob(os.path.join(root, "run_*")) if os.path.isdir(d))
    for d in runs[:-keep] if keep else runs:
        for sub in ["records"] + [os.path.basename(x) for x in glob.glob(os.path.join(d, "java_*"))]:
            shutil.rmtree(os.path.join(d, sub), ignore_errors=True)


def settings_list(args):
    """[(name, numbers file or None, progression file or None)]: --setting folders, else built + --numbers files (each with
    --progression, if given)."""
    pf = os.path.abspath(args.progression) if getattr(args, "progression", None) else None
    if getattr(args, "setting", None):
        return settings_dirs(args.setting)
    out = [("built", None, pf)]
    for p in args.numbers or []:
        out.append((os.path.splitext(os.path.basename(p))[0], os.path.abspath(p), pf))
    if args.numbers and not args.with_built:
        out = out[1:]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--player", action="append", choices=PLAYERS)
    ap.add_argument("--numbers", action="append", help="a numbers.json for the run only (several: one sweep each)")
    ap.add_argument("--with-built", action="store_true", help="with --numbers: also run the shipped numbers.json")
    ap.add_argument("--progression", help="a progression.json for the run only (with --numbers / the shipped numbers)")
    ap.add_argument("--setting", action="append", help="a folder with numbers.json and/or progression.json (several: one sweep "
                    "each; e.g. tools/rpg_soak_settings/as_built)")
    ap.add_argument("--ignore-effects", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--runs", type=int, default=0, help="runs per player type and settings file (0: as many as --hours allows)")
    ap.add_argument("--duels", type=int, default=DUEL_CAP, help="the most duels one run may take")
    ap.add_argument("--after-end", choices=("grind", "stop"), default=AFTER_END,
                    help="after the slice's end: keep grinding the outskirts until --duels (default), or stop")
    ap.add_argument("--rebuild", choices=REBUILD_RULES, default="shape",
                    help="the deck rule after wins and purchases: shape (PROG-TUNE, default: a swap keeps the card type and the "
                         "mana value within 1), tier (RPG-SOAK1's rule) or none (the control: the starting deck all run long)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--hours", type=float, default=0)
    ap.add_argument("--timeout", type=int, default=DUEL_TIMEOUT)
    ap.add_argument("--out")
    ap.add_argument("--summary", help="only (re)write the summary of a finished run folder")
    ap.add_argument("--compare", help="rpg_saves/duel_stats: the bot's win rates next to your own Adventure duels")
    ap.add_argument("--stand-in", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--jobs", help=argparse.SUPPRESS)
    ap.add_argument("--worker", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--seconds", type=float, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.child:
        return child(args)
    if args.summary:
        summ = summarize(read_records(args.summary), expect_forge=not args.stand_in)
        txt = summary_text(summ, f"Adventure soak summary of {args.summary}")
        if args.compare:
            txt += "\n" + compare_text(summ, args.compare)
        print(txt)
        return 0 if summ["verdict"][0] == "VALID" else 1
    players = args.player or list(PLAYERS)
    if not args.runs and not args.hours:
        args.runs = 1
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = args.out or os.path.join(OUT_DIR, f"run_{stamp}")
    os.makedirs(os.path.join(folder, "records"), exist_ok=True)
    prune(OUT_DIR)
    prog = load_progression(args.progression)
    sets = settings_list(args)
    per = args.runs or 1000
    jobs = []
    for i in range(per):
        for s, nf, pf in sets:
            for p in players:
                jobs.append((s, nf, p, args.seed + i, pf))
    w = max(1, args.workers)
    shares = [jobs[i::w] for i in range(w)]
    t0 = time.time()
    procs = []
    for i, share in enumerate(shares):
        if not share:
            continue
        jf = os.path.join(folder, f"jobs_{i}.json")
        with open(jf, "w", encoding="utf-8") as f:
            json.dump(share, f)
        cmd = [sys.executable, os.path.abspath(__file__), "--child", "--jobs", jf, "--worker", str(i),
               "--out", folder, "--duels", str(args.duels), "--timeout", str(args.timeout), "--after-end", args.after_end,
               "--rebuild", args.rebuild] + (["--ignore-effects"] if args.ignore_effects else [])
        if args.hours:
            cmd += ["--seconds", str(args.hours * 3600)]
        if args.stand_in:
            cmd.append("--stand-in")
        procs.append(subprocess.Popen(cmd, cwd=BASE))
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    summ = summarize(read_records(folder), expect_forge=not args.stand_in)
    header = (f"Adventure soak {os.path.basename(folder)}: players {', '.join(players)}; settings "
              f"{', '.join(s for s, _n, _p in sets)}; {prog}; {len(jobs) if args.runs else 'as many runs as fit in ' + str(args.hours) + ' h'}"
              f"; {time.time() - t0:.0f} s")
    txt = summary_text(summ, header)
    if args.compare:
        txt += "\n" + compare_text(summ, args.compare)
    with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, indent=1, default=str)
    print(txt)
    return 0 if summ["verdict"][0] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
