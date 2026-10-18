# SPDX-License-Identifier: GPL-3.0-or-later
"""The RPG's numbers (RPG plan, "Rules of play" and "Numbers still open"): gold, prices, rewards, shop stock, and the region and
its towns.

rpg/data/numbers.json holds every number; rpg/data/towns.json the region's tier band and the towns. Both are PLACEHOLDERS until
Karl plays the slice (the plan leaves prices, the share of gold lost and reward rates open). Each is checked when it is read: a key
that is missing or can't be used is a ValueError naming it - the game never runs on a half-read file.

The arithmetic lives here and nowhere else, so a screen and a test compute the same thing:
  price(tier)              a single's price in gold
  basic_land_price         one basic land in any shop (Round RPG7; 0 while basic_lands_free); shops don't buy them back
  sell_price(tier)         what the shop pays for one copy (a fraction of the price, rounded down, at least 1)
  loss_cost(gold)          the gold a lost duel costs (a share of what you carry, rounded down)
  reward_gold(kind, rng)   the gold a won duel gives (a range per kind of encounter)
"""
import json
import math
import numbers as _num

from rpg import data_path

NUMBERS_FILE = data_path("numbers.json")
TOWNS_FILE = data_path("towns.json")
ENCOUNTERS = ("regular", "dungeon", "boss")
ENEMY_DECK_SOURCES = ("archetype", "jumpstart")   # Round RPG9: which of a kind's deck lists a region's fights use (rpg.enemies)
TIERS = (1, 2, 3, 4, 5)


def _int(v, name, lo=0, hi=None):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{name}: a whole number is needed, not {v!r}")
    if v < lo or (hi is not None and v > hi):
        raise ValueError(f"{name}: {v} is out of range ({lo}..{hi if hi is not None else 'any'})")
    return v


def _share(v, name):
    if isinstance(v, bool) or not isinstance(v, _num.Real) or not (0 <= v <= 1) or math.isnan(v):
        raise ValueError(f"{name}: a share from 0 to 1 is needed, not {v!r}")
    return float(v)


def _range(v, name):
    if not isinstance(v, list) or len(v) != 2:
        raise ValueError(f"{name}: [low, high] is needed, not {v!r}")
    lo, hi = _int(v[0], name + "[0]"), _int(v[1], name + "[1]")
    if lo > hi:
        raise ValueError(f"{name}: low {lo} is above high {hi}")
    return lo, hi


class Numbers:
    """numbers.json, checked. Attributes as in the file; single_price and stock are keyed by tier (int)."""

    def __init__(self, data):
        if not isinstance(data, dict):
            raise ValueError("numbers: a JSON object is needed")
        need = ("save_slots", "start_gold", "loss_gold_share", "basic_lands_free", "single_price", "sell_share", "pack_price",
                "restock_after_duels", "stock", "stock_copies", "shop_skip_prefixes", "rewards")
        missing = [k for k in need if k not in data]
        if missing:
            raise ValueError("numbers: missing " + ", ".join(missing))
        self.save_slots = _int(data["save_slots"], "save_slots", 1, 9)
        self.start_gold = _int(data["start_gold"], "start_gold")
        self.loss_gold_share = _share(data["loss_gold_share"], "loss_gold_share")
        if not isinstance(data["basic_lands_free"], bool):
            raise ValueError("basic_lands_free: true or false")
        self.basic_lands_free = data["basic_lands_free"]
        # Round RPG7 (Karl, 13 Oct: basic lands are bought, "1 gold each"): with basic_lands_free false, basic_land_price is what
        # one basic land costs in any town's shop. It is needed then, and only then.
        if self.basic_lands_free:
            self.basic_land_price = 0
            if "basic_land_price" in data:
                _int(data["basic_land_price"], "basic_land_price")
        else:
            if "basic_land_price" not in data:
                raise ValueError("numbers: missing basic_land_price (basic_lands_free is false)")
            self.basic_land_price = _int(data["basic_land_price"], "basic_land_price", 1)
        prices = data["single_price"]
        if not isinstance(prices, dict) or sorted(prices) != [str(t) for t in TIERS]:
            raise ValueError("single_price: one price for each tier 1-5 is needed")
        self.single_price = {int(t): _int(p, f"single_price[{t}]", 1) for t, p in prices.items()}
        self.sell_share = _share(data["sell_share"], "sell_share")
        self.pack_price = _int(data["pack_price"], "pack_price", 1)
        self.restock_after_duels = _int(data["restock_after_duels"], "restock_after_duels", 1)
        stock = data["stock"]
        if not isinstance(stock, dict) or not stock:
            raise ValueError("stock: {tier: slots} is needed")
        self.stock = {}
        for t, n in stock.items():
            if t not in {str(x) for x in TIERS}:
                raise ValueError(f"stock: {t!r} is not a tier")
            self.stock[int(t)] = _int(n, f"stock[{t}]", 0, 60)
        self.stock_copies = _range(data["stock_copies"], "stock_copies")
        if self.stock_copies[0] < 1:
            raise ValueError("stock_copies: at least one copy a slot")
        skip = data["shop_skip_prefixes"]
        if not isinstance(skip, list) or not all(isinstance(s, str) and s for s in skip):
            raise ValueError("shop_skip_prefixes: a list of words")
        self.shop_skip_prefixes = tuple(skip)
        rewards = data["rewards"]
        if not isinstance(rewards, dict) or sorted(rewards) != sorted(ENCOUNTERS):
            raise ValueError("rewards: regular, dungeon and boss are needed")
        self.rewards = {}
        for kind, r in rewards.items():
            if not isinstance(r, dict) or sorted(r) != ["cards", "gold"]:
                raise ValueError(f"rewards[{kind}]: gold and cards are needed")
            self.rewards[kind] = {"gold": _range(r["gold"], f"rewards[{kind}].gold"),
                                  "cards": _int(r["cards"], f"rewards[{kind}].cards", 0, 15)}

    # ---- the arithmetic ----------------------------------------------------------------------------------------------------

    def price(self, tier):
        """A single's price; None for a card the shop never sells as a single (tier 0 = basic lands - see basic_land_price -,
        None = banned)."""
        return self.single_price.get(tier)

    def sell_price(self, tier):
        p = self.price(tier)
        if p is None:
            return 0
        return max(1, int(p * self.sell_share))

    def loss_cost(self, gold):
        return int(max(0, gold) * self.loss_gold_share)

    def reward_gold(self, kind, rng):
        lo, hi = self.rewards[kind]["gold"]
        return rng.randint(lo, hi)

    def reward_cards(self, kind):
        return self.rewards[kind]["cards"]


def read_numbers(path=NUMBERS_FILE):
    with open(path, encoding="utf-8") as f:
        return Numbers(json.load(f))


class Town:
    __slots__ = ("id", "name", "region", "colours", "sets")

    def __init__(self, d):
        self.id, self.name, self.region = d["id"], d["name"], d["region"]
        self.colours = d.get("colours", "")
        self.sets = list(d.get("sets", []))

    def __repr__(self):
        return f"Town({self.id!r})"


class World:
    """towns.json, checked: the start town, the regions' tier bands and the towns."""

    def __init__(self, data):
        if not isinstance(data, dict):
            raise ValueError("towns: a JSON object is needed")
        regions = data.get("regions")
        if not isinstance(regions, dict) or not regions:
            raise ValueError("towns: regions are needed")
        self.regions = {}
        for rid, r in regions.items():
            band = r.get("tiers") if isinstance(r, dict) else None
            lo, hi = _range(band, f"regions[{rid}].tiers")
            if not (1 <= lo <= hi <= 5):
                raise ValueError(f"regions[{rid}].tiers: a band inside 1-5 is needed, not {band!r}")
            decks = r.get("enemy_decks", {})
            if not isinstance(decks, dict) or any(k not in ENCOUNTERS for k in decks) \
                    or any(v not in ENEMY_DECK_SOURCES for v in decks.values()):
                raise ValueError(f"regions[{rid}].enemy_decks: {{encounter: source}} with encounters from {', '.join(ENCOUNTERS)} "
                                 f"and sources from {', '.join(ENEMY_DECK_SOURCES)} is needed, not {decks!r}")
            self.regions[int(rid)] = {"name": r.get("name", f"Region {rid}"), "tiers": (lo, hi),
                                      "enemy_decks": {e: decks.get(e, "archetype") for e in ENCOUNTERS}}
        towns = data.get("towns")
        if not isinstance(towns, list) or not towns:
            raise ValueError("towns: at least one town is needed")
        self.towns = {}
        for d in towns:
            if not isinstance(d, dict) or not all(k in d for k in ("id", "name", "region")):
                raise ValueError(f"towns: a town needs id, name and region: {d!r}")
            if d["id"] in self.towns:
                raise ValueError(f"towns: {d['id']!r} twice")
            if d["region"] not in self.regions:
                raise ValueError(f"towns: {d['id']!r} is in region {d['region']!r}, which isn't listed")
            colours = d.get("colours", "")
            if not isinstance(colours, str) or any(c not in "WUBRG" for c in colours):
                raise ValueError(f"towns: {d['id']!r} colours must be letters of WUBRG")
            self.towns[d["id"]] = Town(d)
        self.start_town = data.get("start_town")
        if self.start_town not in self.towns:
            raise ValueError(f"towns: start_town {self.start_town!r} isn't one of the towns")

    def town(self, town_id):
        t = self.towns.get(town_id)
        if t is None:
            raise KeyError(f"no town {town_id!r}")
        return t

    def band(self, region):
        return self.regions[region]["tiers"]

    def deck_source(self, region, encounter):
        """Round RPG9: "jumpstart" or "archetype" - which of an enemy kind's deck lists a fight of this kind uses in this region
        (towns.json "enemy_decks"; a region or encounter it doesn't name: "archetype", as before RPG9)."""
        r = self.regions.get(region)
        return (r or {}).get("enemy_decks", {}).get(encounter, "archetype")


def read_world(path=TOWNS_FILE):
    with open(path, encoding="utf-8") as f:
        return World(json.load(f))
