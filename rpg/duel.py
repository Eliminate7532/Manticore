# SPDX-License-Identifier: GPL-3.0-or-later
"""A duel and what comes of it (RPG plan decision 4 and "Rules of play": every duel 1v1 at 20 life; a win gives cards and gold; a
loss costs a share of your gold and sends you back to the last town you visited).

start() checks the player's deck (the RPG's deck rules and that every copy is owned), picks one of the enemy kind's shipped decks
with the save's rng, and marks the duel as going on in the save (pending_duel) - the caller writes the save BEFORE the game starts,
so a duel the program never came back from is known at the next load. What start() returns is everything the table needs: both
deck lists, the format to play (formats "standard" - Forge's Constructed: 20 life, no commander; the RPG's own card rules are
checked here, not by the deck screen's Standard legality) and the enemy's name.

finish(result) applies the result: "won", "lost", "conceded" (a loss), "draw", or "unfinished" (the engine stopped: nothing is
won or lost and the duel can be fought again). Reward cards come from the beaten enemy's own deck - only cards inside the region's
tier band, never basic lands or banned cards, a card the deck plays more copies of more likely - and, when the deck has too few of
those, from the region's band at large. My choice (the plan says only "cards"): Shandalar gave the opponent's cards.

Round RPG9: which of the kind's deck lists a fight uses is the region's choice by kind of encounter (towns.json "enemy_decks",
economy.World.deck_source): on region 1 a regular foe plays one of its Jumpstart decks, a dungeon foe or the boss one of its archetype
decks. pending_duel says which ("source"; a duel marked before RPG9 has none and means the archetype list), so finish() takes the
reward cards from the deck that was actually played - for a Jumpstart foe, its three half-decks' cards, the ones its handicap
swapped for basic lands included (my choice: the best cards of a beaten foe can still be won).

Round PROG2 (Karl, 2 Nov 2026: "pick 1 of 3", "the beaten foe's deck, your colours first"): finish(..., offer=True) - the
Adventure's way - gives no reward cards yet: it deals them as a choice (rpg.rewards.offer_cards: each reward card a group of 3 from
the same deck and band, castable ones first) into save.pending_pick, and rpg.rewards.choose puts the picked ones in. Without offer
(the tools, the older tests) the cards go in at once as before (reward_cards, unchanged - the "before" of PROG2's measurement). A
boss fight marks the highest-tier card of the deck he plays (rpg.rewards.signature_card, "Guards: <card>" on the encounter screen)
in pending_duel["signature"]; beating him gives it on top of his reward cards, either way.

Round PROG3 (levels and perks, rpg.levels): a win's gold gets the perks' bonus, a loss takes the perks' share, and each reward card
is a pick of 1 from (choices_per_card + Keen Eye). The duel's start effects (life by level, the perks' hand and cards) are put on
the Duel by rpg.adventure.start_duel (Duel.effects); the tools that call start() directly get the plain 20-life duel.

Round GOLD1 (Karl, 3 Nov 2026): a loss takes a share of the gold you carry by kind of fight (numbers.json loss_gold_share: regular
5%, dungeon 10%, boss 20%; never more than economy.MAX_LOSS_SHARE), and the foe you lost to holds it (save.world["held_gold"][spot],
losses to the same foe add up) until you beat it: then it comes back on top of the win's gold (out["won_back"]) and the entry goes.
A fight with no spot (a random foe, a rematch, the tools) holds nothing - the gold is simply lost, as before. out["held_by"] is the
spot that holds a loss's gold, out["held"] what it holds now.

settle_pending() is for loading a save that still has a duel going on: it doesn't count (my choice - a crash shouldn't cost gold;
quitting to dodge a loss is possible, and Karl may want it to count as a loss instead).
"""
import time

from rpg import cardpool, enemies, levels, rules
from rpg.collection import problems as deck_problems
from rpg.economy import ENCOUNTERS

FORMAT = "standard"       # formats.py row: 60+ cards, 20 life, Forge's Constructed engine, no commander
LIFE = 20
RESULTS = ("won", "lost", "conceded", "draw", "unfinished")


class DuelError(Exception):
    """A duel that can't start or finish; str() is the sentence for the screen."""


class Duel:
    __slots__ = ("kind", "enemy_name", "encounter", "deck_index", "enemy_deck", "player_deck", "format", "life", "source", "effects")

    def __init__(self, kind, enemy_name, encounter, deck_index, enemy_deck, player_deck, source=enemies.ARCHETYPE):
        self.kind, self.enemy_name, self.encounter = kind, enemy_name, encounter
        self.deck_index, self.enemy_deck, self.player_deck = deck_index, enemy_deck, player_deck
        self.format, self.life = FORMAT, LIFE
        self.source = source                       # Round RPG9: which of the kind's deck lists (rpg.enemies.deck_list)
        self.effects = None                        # Round PROG3: the player's start effects (start_effects.py), None = none

    def player_main(self):
        return [(n, k) for n, k in self.player_deck["main"]]

    def enemy_main(self):
        return [(n, k) for n, k in self.enemy_deck["main"]]


def peek(save, kind_id, encounter="regular", folder=None, source=None):
    """Round RPG13: (deck index, deck) that the next start() for this foe will play, without using up the save's rng (the
    encounter screen's "See its deck"; start(index=) then plays that deck for sure). (None, None) when it has no decks;
    FileNotFoundError when the kind has no file."""
    data = enemies.read_kind(kind_id, folder)
    if source is None:
        source = region_source(save, encounter)
    _source, decks = enemies.deck_list(data, source)
    if not decks:
        return None, None
    i = save.peek_rng("duel:" + kind_id).randrange(len(decks))
    return i, decks[i]


def start(save, kind_id, encounter="regular", pool=None, lists=None, folder=None, now=None, spot=None, once=False, source=None,
          index=None, tier_table=None):
    if encounter not in ENCOUNTERS:
        raise DuelError(f"Unknown kind of fight: {encounter!r}.")
    if save.pending_duel is not None:
        raise DuelError("A duel is already going on.")
    deck = save.deck()
    if deck is None:
        raise DuelError("You have no deck to duel with.")
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    wrong = deck_problems(deck, save.collection, pool, lists)
    if wrong:
        raise DuelError(f"Your deck '{deck['name']}' can't be played: {wrong[0].text}")
    try:
        data = enemies.read_kind(kind_id, folder)
    except FileNotFoundError:
        raise DuelError(f"No enemy decks for {kind_id!r}.")
    if source is None:                 # Round RPG9: the region says which deck list this kind of fight uses (towns.json)
        source = region_source(save, encounter)
    source, decks = enemies.deck_list(data, source)
    if not decks:
        raise DuelError(f"No enemy decks for {kind_id!r}.")
    index_drawn = save.rng("duel:" + kind_id).randrange(len(decks))
    if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(decks)):
        index = index_drawn                  # Round RPG13: index = the deck "See its deck" showed (rpg.duel.peek)
    w = save.world
    save.pending_duel = {"kind": kind_id, "encounter": encounter, "deck": index, "source": source, "map": w.get("map"),
                         "x": w.get("x"), "y": w.get("y"), "started": time.time() if now is None else now}
    if spot:                        # Round RPG5: the map spot this fight is at (rpg.explore), gone once it is won
        save.pending_duel.update(spot=spot, once=bool(once))
    sig = guarded_card(decks[index], encounter, pool, lists, tier_table)
    if sig:                         # Round PROG2: the boss's "Guards: <card>", given on top of his reward cards
        save.pending_duel["signature"] = sig
    return Duel(kind_id, data.get("name", kind_id), encounter, index, decks[index], deck, source)


def guarded_card(deck, encounter, pool, lists, tier_table=None):
    """Round PROG2: the card a boss guards (rpg.rewards.signature_card of the deck he plays), or None for any other fight, when
    progression.json's boss.signature_card is false, or when nothing in his deck has a tier."""
    if encounter != "boss":
        return None
    from rpg import progress, rewards
    try:
        if not progress.read().signature_card:
            return None
    except (OSError, ValueError):
        pass
    if tier_table is None:
        from rpg import tiers
        tier_table = tiers.load(pool=pool)
    return rewards.signature_card(deck, pool, tier_table, lists)


def region_source(save, encounter, world=None):
    """Round RPG9: "jumpstart" or "archetype" for a fight of this kind of encounter in the save's region (towns.json
    "enemy_decks"); "archetype" when the region file can't be read - the enemies as they were before RPG9."""
    try:
        from rpg import economy
        world = world or economy.read_world()
        return world.deck_source(save.world.get("region"), encounter)
    except (OSError, ValueError, KeyError, TypeError):
        return enemies.ARCHETYPE


def _band_ok(name, pool, tier_table, lists, band, skip):
    card = pool.get(name)
    if card is None or card.is_basic or lists.is_banned(name) or any(card.name.startswith(p) for p in skip):
        return False
    t = tier_table.tier_of(card.name)
    return t is not None and band[0] <= t <= band[1]


def reward_cards(save, enemy_deck, n, band, pool, tier_table, lists, numbers):
    """[(name, "deck" | "region")] - n cards for beating `enemy_deck` (see the module's docstring)."""
    if n <= 0:
        return []
    rng = save.rng("reward")
    skip = numbers.shop_skip_prefixes
    weights = {}
    for name, k in list(enemy_deck.get("main", [])) + list(enemy_deck.get("sideboard", [])):
        if _band_ok(name, pool, tier_table, lists, band, skip):
            shown = pool.get(name).name
            weights[shown] = weights.get(shown, 0) + int(k)
    out = []
    while len(out) < n and weights:
        names = sorted(weights)
        pick = rng.choices(names, weights=[weights[x] for x in names])[0]
        del weights[pick]
        out.append((pick, "deck"))
    if len(out) < n:
        from rpg.shop import candidates
        tiers_in_band = [t for t in range(band[0], band[1] + 1)]
        while len(out) < n:
            t = rng.choice(tiers_in_band)
            names = [x for x in candidates(t, "", pool, tier_table, lists, skip) if x not in {o[0] for o in out}]
            if not names:
                tiers_in_band.remove(t)
                if not tiers_in_band:
                    break
                continue
            out.append((rng.choice(names), "region"))
    return out


def finish(save, result, numbers, world, pool=None, tier_table=None, lists=None, folder=None, offer=False):
    """Apply a duel's result to the save; returns {"result", "gold" (+won / -lost), "cards": [(name, from)], "moved_to"}.
    Round PROG2: with offer, a win's reward cards are a choice instead (out["offers"]: [[name, ...], ...], save.pending_pick); a
    boss's signature card is in out["cards"] as (name, "signature") either way."""
    if result not in RESULTS:
        raise DuelError(f"Unknown result {result!r}.")
    pd = save.pending_duel
    if pd is None:
        raise DuelError("No duel is going on.")
    out = {"result": result, "gold": 0, "cards": [], "moved_to": None}
    save.pending_duel = None
    if result == "unfinished":
        return out
    rec = save.record
    rec["duels"] += 1
    if result == "won":
        pool = pool or cardpool.load()
        lists = lists or rules.vintage_lists()
        if tier_table is None:
            from rpg import tiers
            tier_table = tiers.load(pool=pool)
        rec["wins"] += 1
        rec["defeated"][pd["kind"]] = rec["defeated"].get(pd["kind"], 0) + 1
        gold = numbers.reward_gold(pd["encounter"], save.rng("gold"))
        gold = _perk(lambda lv: levels.win_gold(save, gold, lv), gold)   # PROG3: Bounty Hunter
        back = held_by(save, pd.get("spot"))         # Round GOLD1: the gold this foe took from you, back on top (no perk bonus:
        if back:                                     # ... it is your own gold), given once - the entry goes
            held_gold(save).pop(pd["spot"], None)
            if not save.world["held_gold"]:
                save.world.pop("held_gold")
            out["won_back"] = back
        gold += back
        save.gold += gold
        try:
            deck = enemies.deck_list(enemies.read_kind(pd["kind"], folder), pd.get("source", enemies.ARCHETYPE))[1][pd["deck"]]
        except (OSError, KeyError, IndexError):
            deck = {"main": []}
        if deck.get("swapped"):        # Round RPG9: the cards a Jumpstart foe's handicap took out of its half-decks can be won too
            deck = dict(deck, main=[list(x) for x in deck["main"]] + [list(x) for x in deck["swapped"]])
        from rpg import progress                     # Round PROG1: the results screen's NEW! - told before the cards go in
        cards, offers = [], []
        sig = pd.get("signature")                    # Round PROG2: the boss's signature card, on top of his reward cards
        if isinstance(sig, str) and pool.get(sig) is not None:
            cards.append((pool.get(sig).name, "signature"))
        band, many = world.band(save.world["region"]), numbers.reward_cards(pd["encounter"])
        if offer:                                    # Round PROG2: each reward card a choice of 3 (rpg.rewards)
            from rpg import rewards
            try:
                per = progress.read().choices_per_card
            except (OSError, ValueError):
                per = 3
            per = _perk(lambda lv: levels.reward_choices(save, per, lv), per)  # PROG3: Keen Eye
            offers = rewards.offer_cards(save, deck, many, band, pool, tier_table, lists, numbers,
                                         rewards.deck_colours(save.deck(), pool), per=per)
        else:
            cards += reward_cards(save, deck, many, band, pool, tier_table, lists, numbers)
        new = [name for name, _src in cards if progress.is_new(save, name)]
        for name, _src in cards:
            save.collection.add(name, 1)
        out.update(gold=gold, cards=cards, new=new)
        if offers:
            save.pending_pick = {"groups": offers, "kind": pd["kind"], "encounter": pd.get("encounter")}
            out["offers"] = offers
        if pd.get("spot"):
            gone = save.world.setdefault("cleared" if pd.get("once") else "beaten", [])
            if pd["spot"] not in gone:
                gone.append(pd["spot"])
        from rpg import talk                         # Round RPG14: the boss flag, and the quests that count this kind
        talk.on_duel_won(save.world, pd["kind"], pd.get("encounter"), spot=pd.get("spot"))   # RPG18b: a goal may name a spot
    elif result in ("lost", "conceded"):
        rec["losses"] += 1
        cost = loss_cost(save, numbers, pd.get("encounter") or "regular")   # PROG3: Thick Skin; GOLD1: by kind of fight
        save.gold -= cost
        spot = pd.get("spot")                         # Round GOLD1: the foe holds what it took, until you beat it
        if spot and cost > 0:
            held = save.world.get("held_gold")
            if not isinstance(held, dict):
                held = save.world["held_gold"] = {}
            held[spot] = held_by(save, spot) + cost
            out.update(held_by=spot, held=held[spot])
        town = save.world.get("last_town") or world.start_town
        save.world.update(map=town, x=None, y=None)
        out.update(gold=-cost, moved_to=town)
        inn = save.world.get("inn")                   # Round RPG14: you wake at the inn you last rested at
        if isinstance(inn, dict) and inn.get("map") and inn.get("spawn"):
            save.world.update(map=inn["map"], x=None, y=None, spawn=inn["spawn"])
            save.world.pop("in_town", None)
            out["inn"] = True
            # Round RPG17 (Karl 24 Oct: "Losses keep the clock"): no longer on to 07:00 (RPG15 did that, so a player who lost
            # at night never saw night again - claude/SLICE_ECONOMY_2026-10-24.md 10.3: 0 night fights in 2,495 duels)
            save.world["beaten"] = []                 # Round RPG15c (review N3): a night at the inn - the beaten foes are back,
                                                      # as after a rest there (RPG15b) and a loss that wakes you in a town
    else:
        rec["draws"] += 1
    return out


def _perk(f, default):
    """Round PROG3: f(levels table) with the perks, or `default` when progression.json can't be read (the duel never stops on it)."""
    try:
        return f(levels.read())
    except (OSError, ValueError):
        return default


def loss_cost(save, numbers, kind="regular"):
    """What a loss of this kind of fight takes now (numbers.json's share for the kind, made smaller by a perk - rpg.levels.loss_cost);
    the encounter screen says the same. Round GOLD1: by kind of fight, never over economy.MAX_LOSS_SHARE."""
    return _perk(lambda lv: levels.loss_cost(save, numbers, kind, lv), numbers.loss_cost(save.gold, kind))


def held_gold(save):
    """Round GOLD1: {spot: gold} - the gold each foe on the map holds since it beat you ({} for none)."""
    held = save.world.get("held_gold")
    return held if isinstance(held, dict) else {}


def held_by(save, spot):
    """Round GOLD1: the gold the foe at this map spot holds (0 for none, or for a fight with no spot)."""
    n = held_gold(save).get(spot) if spot else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def settle_pending(save):
    """On loading: a duel still marked as going on doesn't count. Returns a sentence for the screen, or None."""
    pd = save.pending_duel
    if pd is None:
        return None
    save.pending_duel = None
    return "Your last duel didn't finish, so it doesn't count - nothing was won or lost."


def visit_town(save, town_id, world):
    """Entering a town: it becomes the last town (where a loss sends you) and is remembered as visited."""
    world.town(town_id)
    w = save.world
    w["last_town"] = town_id
    if town_id not in w["towns_visited"]:
        w["towns_visited"].append(town_id)
    w["beaten"] = []                # Round RPG5: the map's beaten (not "once") foes come back after a visit to a town
