# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's progression, part 5: EXTRA SOURCES of gold, XP and cards (Round PROG5; Karl, 2 Nov 2026, answer 10: "All four" -
first win of the day bonus, bounty board, trade-up, town gauntlet; the design is the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md
section 3.5). No pygame: rpg_screens.py draws; every rule is here, once. Every number is in rpg/data/progression.json ("first_win",
"bounties", "trade_up", "gauntlet"), Claude's placeholders until prog-tune measures them.

    read(path)                       the four tables of progression.json, checked (a key it can't use is a ValueError naming it)
    first_win(save, result, now)     THE first-win rule: the first won duel of an in-game day (rpg.clock.day_number) -> its bonus
    post_bounties(save, now)         the board's jobs as they stand today: slots jobs at a time, one new job each new in-game day
    bounty_on_win(world, kind, map_name, phase)  a won duel counts for every job whose goal it meets
    collect_bounties(game)           the jobs done are paid (gold, XP or a card in your colours) and leave the board
    board_pages(save)                what the bounty clerk says about the board
    trade_rows(...) / trade_up(...)  the card shop's Trade up: copies of a tier-N card -> 1 random tier-N+1 card in your colours
    gauntlet_state / enter_gauntlet / after_gauntlet_duel / stop_gauntlet   the town gauntlet

What the save keeps (all in save.world, so an older save simply has none of them - nothing to migrate):
  "first_win_day"  the in-game day whose first win was paid
  "bounties"       {"day": the last day a job was posted, "next": a counter for job ids, "jobs": [{"id", "job" (the table's key),
                   "count" (wins counted), "posted" (day)}]}
  "gauntlet"       {"fee", "played", "wins", "foes": [kind, ...]} while a gauntlet is under way (absent otherwise)

Readings of mine (Karl's to change; each is one place):
  - First win: any won Adventure duel counts (a gauntlet's too); its gold and XP come on top of the duel's own and are said on
    the results screen. The day is the clock's "Day N" (a day turns at midnight; sleeping at the inn moves the clock on to 07:00).
  - Bounty board: all its jobs are under way at once (no taking them); a job done is paid the next time you talk to the clerk.
    "A new one each in-game day": each new day posts one job - into a free place, or in place of the oldest job not yet done.
    A first visit fills the board. A job's goal uses quests.json's words: {"beat": kind, "count": n}; and two more, {"win": n,
    "phase": "night"} (won at night) and {"win": n, "map": "warrens_1|warrens_2"} (won on one of those maps).
  - Trade-up: the copies traded may come out of saved decks (as selling since SELL1, the sideboard first); the card got is a
    random card of the next tier that your deck can cast (rpg.rewards.castable), any colour only when there is none; never above
    the region's band (region 1: tier 1 -> 2 and 2 -> 3).
  - Gauntlet: its duels pay no foe's gold or cards - only the prizes by wins once the last duel is played (XP per duel as usual, and
    a first win of the day counts). A loss there takes no gold and doesn't send you to the inn. Stopping early ("Stop here") pays
    the prize for the wins so far. A program closed in the middle RESUMES: the gauntlet is in the save, and the warden asks for the
    next duel (a duel itself left unfinished doesn't count and is fought again, as every unfinished duel). Never a refund.
"""
import json

from rpg import clock, data_path

PROGRESSION_FILE = data_path("progression.json")

BOUNTY_CLERK = "bounty_clerk"
GAUNTLET_WARDEN = "gauntlet_warden"

# The words (Karl to edit; every one is listed in the round's doc)
FIRST_WIN_LINE = "First win of the day: +{gold} gold and +{xp} XP."
BOARD_EMPTY = "The board is bare today. Come back tomorrow: a new job goes up every morning."
BOARD_JOB = "{n}. {text} ({done} of {count}). Pays {reward}."
BOARD_NEW = "New today: {text}."
BOARD_PAID = "Paid for \"{text}\": {what}"
BOUNTY_DONE_LINE = "Bounty done: {text}. The clerk in Lantern Hollow pays."
BOARD_TOMORROW = "A new job goes up each day. Jobs count by themselves; come back to be paid."
TRADE_TITLE = "Trade up  -  {copies} copies for 1 card a tier higher; a card your deck uses comes out of the deck"
TRADE_NONE = "Nothing to trade: you need {copies} copies of a card of tier {tiers}."
TRADE_DONE = "Traded {copies} {name} for {got} (tier {tier})."
GAUNTLET_OFFER = "The gauntlet: {duels} duels in a row against the road's foes. Entry {fee} gold."
GAUNTLET_PRIZES = "Win 1: {p1}. Win 2: {p2}. Win all {duels}: {p3}. A loss here costs no gold."
GAUNTLET_NEXT = "Duel {n} of {duels}. You've won {wins}. Ready?"
GAUNTLET_ENTER, GAUNTLET_NOT_NOW = "Enter: {fee} gold", "Not now"
GAUNTLET_GO, GAUNTLET_STOP = "Next duel", "Stop here"
GAUNTLET_POOR = "The entry is {fee} gold; you have {gold}."
GAUNTLET_DUEL_LINE = "Gauntlet duel {n} of {duels}: {result}. Wins so far: {wins}."
GAUNTLET_DONE_LINE = "The gauntlet is over: {wins} of {duels} won."
GAUNTLET_NOTHING = "No prize this time."
GAUNTLET_LOSS_LINE = "A gauntlet loss costs no gold."
GAUNTLET_WIN = "Win: a step toward the gauntlet's prize (no gold or cards from the foe)."
GAUNTLET_LOSE = "Lose: nothing - a gauntlet loss costs no gold."


def _whole(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"progression.json {name}: a whole number from {lo} to {hi} is needed, not {v!r}")
    return v


REWARD_KEYS = ("gold", "xp", "card", "pack")


def _reward(v, where, allow_pack=False):
    if not isinstance(v, dict):
        raise ValueError(f"{where}: a table is needed")
    keys = REWARD_KEYS if allow_pack else ("gold", "xp", "card")
    bad = [k for k in v if k not in keys]
    if bad:
        raise ValueError(f"{where}: {bad[0]!r} isn't one of {', '.join(keys)}")
    out = {}
    for k in ("gold", "xp"):
        if k in v:
            out[k] = _whole(v[k], f"{where}.{k}", 0, 100000)
    if "card" in v:
        if v["card"] not in ("colours", "rare_from_band"):
            raise ValueError(f"{where}.card: \"colours\" (a card from the band your deck can cast) or \"rare_from_band\", "
                             f"not {v['card']!r}")
        out["card"] = v["card"]
    if "pack" in v:
        if v["pack"] is not True:
            raise ValueError(f"{where}.pack: true (one of the town's packs, opened at once), not {v['pack']!r}")
        out["pack"] = True
    return out


GOAL_KEYS = ("beat", "count", "win", "phase", "map")


def _goal(v, where):
    if not isinstance(v, dict):
        raise ValueError(f"{where}: a table is needed")
    bad = [k for k in v if k not in GOAL_KEYS]
    if bad:
        raise ValueError(f"{where}: {bad[0]!r} isn't one of {', '.join(GOAL_KEYS)}")
    if "beat" in v:
        if not isinstance(v["beat"], str) or not v["beat"]:
            raise ValueError(f"{where}.beat: an enemy kind's id")
        return {"beat": v["beat"], "count": _whole(v.get("count", 1), where + ".count", 1, 20)}
    if "win" not in v:
        raise ValueError(f"{where}: a goal says beat (a kind) or win (a number of duels)")
    out = {"win": _whole(v["win"], where + ".win", 1, 20)}
    if "phase" in v:
        ph = v["phase"]
        if not isinstance(ph, str) or not all(p in ("dawn", "day", "dusk", "night") for p in ph.split("|")):
            raise ValueError(f"{where}.phase: dawn, day, dusk or night (| between them), not {ph!r}")
        out["phase"] = ph
    if "map" in v:
        if not isinstance(v["map"], str) or not v["map"]:
            raise ValueError(f"{where}.map: map names (| between them)")
        out["map"] = v["map"]
    return out


class Extras:
    def __init__(self, d):
        if not isinstance(d, dict):
            raise ValueError("progression.json: a table is needed")
        fw, bo, tu, ga = (d.get(k, {}) for k in ("first_win", "bounties", "trade_up", "gauntlet"))
        if not all(isinstance(x, dict) for x in (fw, bo, tu, ga)):
            raise ValueError("progression.json: first_win, bounties, trade_up and gauntlet must be tables (Round PROG5)")
        self.first_gold = _whole(fw.get("gold", 0), "first_win.gold", 0, 100000)
        self.first_xp = _whole(fw.get("xp", 0), "first_win.xp", 0, 100000)
        self.slots = _whole(bo.get("slots", 3), "bounties.slots", 1, 6)
        jobs = bo.get("jobs", {})
        if not isinstance(jobs, dict):
            raise ValueError("progression.json bounties.jobs: a table of job id -> job is needed")
        self.jobs = {}
        for jid, j in jobs.items():
            where = f"progression.json bounties.jobs.{jid}"
            if not isinstance(j, dict) or not isinstance(j.get("text"), str) or not j["text"].strip():
                raise ValueError(f"{where}: a text is needed")
            bad = [k for k in j if k not in ("text", "goal", "reward")]
            if bad:
                raise ValueError(f"{where}: {bad[0]!r} isn't one of text, goal, reward")
            rw = _reward(j.get("reward"), where + ".reward")
            if not rw:
                raise ValueError(f"{where}.reward: a job pays something")
            self.jobs[jid] = {"text": j["text"].strip(), "goal": _goal(j.get("goal"), where + ".goal"), "reward": rw}
        self.trade_copies = _whole(tu.get("copies", 4), "trade_up.copies", 2, 20)
        self.trade_past_band = tu.get("past_band", False)
        if not isinstance(self.trade_past_band, bool):
            raise ValueError("progression.json trade_up.past_band: true or false")
        self.fee = _whole(ga.get("fee", 50), "gauntlet.fee", 0, 100000)
        self.duels = _whole(ga.get("duels", 3), "gauntlet.duels", 1, 10)
        foes = ga.get("foes", [])
        if not isinstance(foes, list) or not foes or not all(isinstance(f, str) and f for f in foes):
            raise ValueError("progression.json gauntlet.foes: a list of enemy kinds is needed")
        self.foes = list(foes)
        pr = ga.get("prizes", {})
        if not isinstance(pr, dict):
            raise ValueError("progression.json gauntlet.prizes: a table of wins -> prize is needed")
        self.prizes = {}
        for k, v in pr.items():
            if not str(k).isdigit() or not 0 <= int(k) <= self.duels:
                raise ValueError(f"progression.json gauntlet.prizes: {k!r} isn't a number of wins from 0 to {self.duels}")
            self.prizes[int(k)] = _reward(v, f"progression.json gauntlet.prizes.{k}", allow_pack=True)

    def prize(self, wins):
        return dict(self.prizes.get(int(wins), {}))


_READ = {}


def read(path=PROGRESSION_FILE):
    if path not in _READ:
        with open(path, encoding="utf-8") as f:
            _READ[path] = Extras(json.load(f))
    return _READ[path]


def reward_words(rw, numbers=None):
    """"40 gold", "25 XP", "a card in your colours", "50 gold and a pack" ... ("nothing" for none)."""
    parts = []
    if rw.get("gold"):
        parts.append(f"{rw['gold']} gold")
    if rw.get("xp"):
        parts.append(f"{rw['xp']} XP")
    if rw.get("pack"):
        parts.append("a pack")
    if rw.get("card") == "colours":
        parts.append("a card in your colours")
    elif rw.get("card") == "rare_from_band":
        parts.append("a rare card")
    if not parts:
        return "nothing"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _day(world, now=None):
    return clock.day_number(clock.now(world) if now is None else now)


# ---- 1. first win of the day ----------------------------------------------------------------------------------------------------

def first_win(save, result, now=None, ex=None):
    """THE first-win rule: a won duel on a day whose first win isn't paid yet -> {"gold", "xp", "day"} (the save marked; the caller
    gives the gold and the XP), else None."""
    if result != "won":
        return None
    ex = ex or read()
    if not (ex.first_gold or ex.first_xp):
        return None
    day = _day(save.world, now)
    last = save.world.get("first_win_day")
    if isinstance(last, int) and not isinstance(last, bool) and last >= day:
        return None
    save.world["first_win_day"] = day
    return {"gold": ex.first_gold, "xp": ex.first_xp, "day": day}


# ---- 2. the bounty board ---------------------------------------------------------------------------------------------------------

def _board(world):
    b = world.get("bounties")
    if not isinstance(b, dict) or not isinstance(b.get("jobs"), list):
        b = world["bounties"] = {"day": 0, "next": 0, "jobs": []}
    b["jobs"] = [j for j in b["jobs"] if isinstance(j, dict) and isinstance(j.get("job"), str)]
    return b


def _job_goal(ex, j):
    return (ex.jobs.get(j["job"]) or {}).get("goal") or {}


def job_need(ex, j):
    g = _job_goal(ex, j)
    return int(g.get("count", g.get("win", 1)))


def job_done(ex, j):
    return int(j.get("count", 0)) >= job_need(ex, j)


def _new_job(save, b, ex, day):
    """One job drawn from the table, not one already on the board (when the table has more)."""
    have = {j["job"] for j in b["jobs"]}
    ids = sorted(ex.jobs)
    pool = [i for i in ids if i not in have] or ids
    if not pool:
        return None
    pick = save.rng("bounty").choice(pool)
    b["next"] = int(b.get("next", 0)) + 1
    return {"id": f"b{b['next']}", "job": pick, "count": 0, "posted": day}


def post_bounties(save, now=None, ex=None):
    """The board as it stands today. A first visit fills it; then each new in-game day posts one job (into a free place, or in place
    of the oldest job not done), at most `slots` jobs a day passed. Returns the jobs posted now."""
    ex = ex or read()
    w = save.world
    b = _board(w)
    day = _day(w, now)
    posted = []
    if not b["jobs"] and not b.get("day"):                 # a first visit: a full board
        for _ in range(ex.slots):
            j = _new_job(save, b, ex, day)
            if j:
                b["jobs"].append(j)
                posted.append(j)
        b["day"] = day
        return posted
    last = int(b.get("day") or 0)
    for d in range(last + 1, day + 1)[-ex.slots:] if day > last else ():
        if len(b["jobs"]) >= ex.slots:
            old = [j for j in b["jobs"] if not job_done(ex, j)]
            if not old:
                break                                       # every job is done and waiting to be paid: nothing goes
            b["jobs"].remove(min(old, key=lambda j: (int(j.get("posted", 0)), j["id"])))
        j = _new_job(save, b, ex, d)
        if j:
            b["jobs"].append(j)
            posted.append(j)
    b["day"] = max(last, day)
    return posted


def counts(goal, kind, map_name, phase):
    """A won duel against `kind` on `map_name` at `phase` counts for this goal."""
    if "beat" in goal:
        return goal["beat"] == kind
    if "phase" in goal and phase not in goal["phase"].split("|"):
        return False
    if "map" in goal and map_name not in goal["map"].split("|"):
        return False
    return True


def bounty_on_win(world, kind, map_name, phase, ex=None):
    """A won duel: one more on every job on the board whose goal it meets (a job done counts no further). Returns the jobs it
    finished now."""
    ex = ex or read()
    b = world.get("bounties")
    if not isinstance(b, dict) or not isinstance(b.get("jobs"), list):
        return []
    done = []
    for j in b["jobs"]:
        if not isinstance(j, dict) or j.get("job") not in ex.jobs or job_done(ex, j):
            continue
        if counts(_job_goal(ex, j), kind, map_name, phase):
            j["count"] = int(j.get("count", 0)) + 1
            if job_done(ex, j):
                done.append(j)
    return done


def job_texts(jobs, ex=None):
    ex = ex or read()
    return [ex.jobs[j["job"]]["text"] for j in jobs if j.get("job") in ex.jobs]


def collect_bounties(game, ex=None, now=None):
    """The jobs done are paid and leave the board. Returns [(job text, [lines that say what was given])]."""
    ex = ex or read()
    s = game.save
    b = _board(s.world)
    out = []
    for j in list(b["jobs"]):
        if j.get("job") in ex.jobs and job_done(ex, j):
            spec = ex.jobs[j["job"]]
            b["jobs"].remove(j)
            out.append((spec["text"], give(game, spec["reward"])))
    return out


def give(game, rw):
    """A reward table into the save ({"gold", "xp", "card": "colours" | "rare_from_band", "pack": true}); returns the lines."""
    from rpg import adventure as adv
    s, lines = game.save, []
    if rw.get("gold"):
        s.gold += int(rw["gold"])
        lines.append(f"+{int(rw['gold'])} gold.")
    if rw.get("pack"):
        lines += _give_pack(game)
    if rw.get("card") == "colours":
        from rpg import chests, rewards
        pick = chests._band_card(s, game.pool, game.tiers(), game.lists(), game.numbers,
                                 game.world.band(s.world.get("region") or 1), rewards.deck_colours(s.deck(), game.pool),
                                 s.rng("bounty_card"))
        if pick:
            s.collection.add(pick, 1)
            lines.append(f"New card: {pick}.")
    elif rw.get("card") == "rare_from_band":
        lines += adv.give_reward(game, {"card": "rare_from_band"})
    if rw.get("xp"):
        from rpg import levels
        try:
            lv = levels.read()
            g = levels.gain(s, int(rw["xp"]), lv)
            lines.append(f"+{g['gained']} XP.")
            lines += levels.level_up_lines(g, lv)
        except (OSError, ValueError):
            pass
    return lines


def _give_pack(game):
    """One of the start town's packs (its sets with a pack pool), opened at once."""
    from rpg import products
    s = game.save
    town = s.world.get("last_town") or game.world.start_town
    try:
        sets = [c for c in game.world.town(town).sets if products.has_packs(c)]
    except KeyError:
        sets = []
    if not sets:
        return []
    code = s.rng("prize_pack").choice(sets)
    got = []
    for name, _r, _f in products.open_pack(code, s.rng("pack:" + code)):
        card = game.pool.get(name)
        shown = card.name if card is not None else name
        s.collection.add(shown, 1)
        got.append(shown)
    return [f"A {products.read_pool(code).get('name', code)} pack: " + ", ".join(got) + "."]


def board_lines(save, ex=None):
    """[(text, done, count, reward words)] of the jobs on the board, in the order posted."""
    ex = ex or read()
    b = save.world.get("bounties")
    out = []
    for j in (b.get("jobs") if isinstance(b, dict) and isinstance(b.get("jobs"), list) else []):
        spec = ex.jobs.get(j.get("job")) if isinstance(j, dict) else None
        if spec:
            out.append((spec["text"], min(int(j.get("count", 0)), job_need(ex, j)), job_need(ex, j), reward_words(spec["reward"])))
    return out


def board_pages(save, posted=(), paid=(), ex=None):
    """What the clerk says: what was paid now, what went up today, then each job (one page each)."""
    ex = ex or read()
    pages = []
    for text, given in paid:
        pages.append(BOARD_PAID.format(text=text, what=" ".join(given) or "nothing."))
    for j in posted[:1] if len(posted) == 1 else ():
        spec = ex.jobs.get(j["job"])
        if spec:
            pages.append(BOARD_NEW.format(text=spec["text"]))
    rows = board_lines(save, ex)
    for i, (text, done, count, words) in enumerate(rows, 1):
        pages.append(BOARD_JOB.format(n=i, text=text, done=done, count=count, reward=words))
    if not rows:
        pages.append(BOARD_EMPTY)
    else:
        pages.append(BOARD_TOMORROW)
    return pages


def journal_lines(save, ex=None):
    """The Menu's quest list: [(title, line)] for each job on the board."""
    try:
        rows = board_lines(save, ex)
    except (OSError, ValueError):
        return []
    return [(f"Bounty: {t}", ("Done! Go back to the board." if d >= c else f"{d} of {c}")) for t, d, c, _w in rows]


# ---- 3. trade-up -----------------------------------------------------------------------------------------------------------------

def trade_tiers(band, ex=None):
    """{from tier: to tier} the card shop trades in this band (region 1, band 1-3: {1: 2, 2: 3})."""
    ex = ex or read()
    lo, hi = band
    return {t: t + 1 for t in range(lo, hi + (1 if ex.trade_past_band else 0)) if t >= 1}


def trade_rows(save, tier_table, band, uses=None, ex=None):
    """[(name, tier, owned, deck uses)] - the cards you could trade now: at least `copies` owned (never a free basic), a tier the band
    trades up from."""
    ex = ex or read()
    tiers = trade_tiers(band, ex)
    uses = uses or {}
    from rpg import cardpool
    out = []
    for name in save.collection.names():
        if save.collection.is_free(name):
            continue
        have = save.collection.count(name) or 0
        t = tier_table.tier_of(name)
        if have >= ex.trade_copies and t in tiers:
            out.append((name, t, have, uses.get(cardpool.normal_name(name), 0)))
    return sorted(out, key=lambda r: (r[1], r[0].lower()))


class TradeError(Exception):
    """A trade that can't be made; str() is the sentence for the screen."""


def trade_up(save, name, pool, tier_table, lists, numbers, band, ex=None):
    """Trade `copies` of `name` for 1 random card of the next tier. Returns (the card got, its tier, [(deck name, copies taken)]).
    Copies a saved deck uses that you'd no longer own come out of that deck (the sideboard first, as a sale does)."""
    ex = ex or read()
    from rpg import rewards
    from rpg.collection import deck_takes, take_out
    from rpg.shop import candidates
    k = ex.trade_copies
    if save.collection.is_free(name):
        raise TradeError(f"{name} is a free basic land - nothing to trade.")
    have = save.collection.count(name) or 0
    if have < k:
        raise TradeError(f"You own {have} {name}; a trade takes {k}.")
    t = tier_table.tier_of(name)
    to = trade_tiers(band, ex).get(t)
    if to is None:
        raise TradeError(f"{name} is tier {t}: this shop trades up tiers {', '.join(map(str, sorted(trade_tiers(band, ex))))}.")
    names = candidates(to, "", pool, tier_table, lists, numbers.shop_skip_prefixes)
    colours = rewards.deck_colours(save.deck(), pool)
    mine = [n for n in names if rewards.castable(pool.get(n), colours)]
    rng = save.rng("trade_up")
    if not (mine or names):
        raise TradeError(f"There is no tier {to} card to trade for.")
    got = rng.choice(mine or names)
    takes = deck_takes(name, k, save.collection, save.decks)
    take_out(takes, name)
    taken = {}
    for d, _part, n in takes:
        taken[d.get("name", "?")] = taken.get(d.get("name", "?"), 0) + n
    save.collection.remove(name, k)
    shown = pool.get(got).name if pool.get(got) is not None else got
    save.collection.add(shown, 1)
    return shown, to, sorted(taken.items())


# ---- 4. the town gauntlet --------------------------------------------------------------------------------------------------------

def gauntlet_state(world):
    g = world.get("gauntlet")
    if not isinstance(g, dict):
        return None
    try:
        played, wins = int(g.get("played", 0)), int(g.get("wins", 0))
    except (TypeError, ValueError):
        return None
    foes = g.get("foes") if isinstance(g.get("foes"), list) else []
    return {"played": played, "wins": wins, "fee": int(g.get("fee", 0) or 0), "foes": foes}


def enter_gauntlet(save, ex=None):
    """Pay the entry and start a gauntlet: its foes drawn now (a regular foe kind for each duel). TradeError (a sentence) when you
    can't pay or one is under way."""
    ex = ex or read()
    if gauntlet_state(save.world):
        raise TradeError("A gauntlet is already under way.")
    if save.gold < ex.fee:
        raise TradeError(GAUNTLET_POOR.format(fee=ex.fee, gold=save.gold))
    rng = save.rng("gauntlet")
    foes = [rng.choice(ex.foes) for _ in range(ex.duels)]
    save.gold -= ex.fee
    save.world["gauntlet"] = {"fee": ex.fee, "played": 0, "wins": 0, "foes": foes}
    return save.world["gauntlet"]


def next_foe(world, ex=None):
    """The enemy kind of the gauntlet's next duel, or None."""
    ex = ex or read()
    g = gauntlet_state(world)
    if not g or g["played"] >= ex.duels:
        return None
    foes = g["foes"]
    return foes[g["played"]] if g["played"] < len(foes) and isinstance(foes[g["played"]], str) else ex.foes[0]


def after_gauntlet_duel(game, result, ex=None):
    """A gauntlet duel settled ("won", "lost", "conceded", "draw"; "unfinished" doesn't count). Returns {"n", "wins", "done",
    "lines"} - the prize lines once the last duel is played (the gauntlet then leaves the save)."""
    ex = ex or read()
    w = game.save.world
    g = w.get("gauntlet")
    if not isinstance(g, dict) or result not in ("won", "lost", "conceded", "draw"):
        return None
    g["played"] = int(g.get("played", 0)) + 1
    if result == "won":
        g["wins"] = int(g.get("wins", 0)) + 1
    out = {"n": g["played"], "wins": g["wins"], "done": g["played"] >= ex.duels,
           "lines": [GAUNTLET_DUEL_LINE.format(n=g["played"], duels=ex.duels, result={"won": "won", "draw": "a draw"}.get(
               result, "lost"), wins=g["wins"])]}
    if result != "won":
        out["lines"].append(GAUNTLET_LOSS_LINE)
    if out["done"]:
        out["lines"] += finish_gauntlet(game, ex)
    return out


def finish_gauntlet(game, ex=None):
    """The prize for the wins so far; the gauntlet leaves the save. Returns the lines."""
    ex = ex or read()
    w = game.save.world
    g = gauntlet_state(w)
    w.pop("gauntlet", None)
    if not g:
        return []
    lines = [GAUNTLET_DONE_LINE.format(wins=g["wins"], duels=ex.duels)]
    given = give(game, ex.prize(g["wins"]))
    return lines + (given or [GAUNTLET_NOTHING])


def stop_gauntlet(game, ex=None):
    """"Stop here": the prize for the wins so far (the duels not fought count as not won)."""
    return finish_gauntlet(game, ex)


def gauntlet_pages(world, ex=None):
    """(pages, choice) the warden adds: the offer and its prizes, or the next duel."""
    ex = ex or read()
    g = gauntlet_state(world)
    if g:
        return [GAUNTLET_NEXT.format(n=g["played"] + 1, duels=ex.duels, wins=g["wins"])], (GAUNTLET_GO, GAUNTLET_STOP)
    p = {k: reward_words(ex.prize(k)) for k in range(ex.duels + 1)}
    return ([GAUNTLET_OFFER.format(duels=ex.duels, fee=ex.fee),
             GAUNTLET_PRIZES.format(p1=p.get(1), p2=p.get(2), p3=p.get(ex.duels), duels=ex.duels)],
            (GAUNTLET_ENTER.format(fee=ex.fee), GAUNTLET_NOT_NOW))


def split_pages(texts, width=34, lines=3):
    """Pages for the dialogue box: each text wrapped at `width` characters (rpg_ui.wrap_lines' rule) and cut into pages of at most
    `lines` lines - the box draws every line it is given, so a long one would run out of it."""
    import re
    import textwrap

    def fits(t):
        return len(textwrap.wrap(t, width)) <= lines

    out = []
    for t in texts:
        page = ""
        for sentence in re.split(r"(?<=[.!?])\s+", str(t).strip()):      # whole sentences on a page where they fit
            joined = f"{page} {sentence}".strip()
            if fits(joined):
                page = joined
                continue
            if page:
                out.append(page)
            ws = textwrap.wrap(sentence, width) or []
            while len(ws) > lines:                                       # a sentence longer than a page: cut by lines
                out.append(" ".join(ws[:lines]))
                ws = ws[lines:]
            page = " ".join(ws)
        if page:
            out.append(page)
    return out
