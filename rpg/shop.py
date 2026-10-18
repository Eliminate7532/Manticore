# SPDX-License-Identifier: GPL-3.0-or-later
"""A town's shops (RPG plan decision 12, "Singles and booster packs"; "Rules of play": singles from the town's tier band, priced in
gold by tier; booster packs from the town's list of sets; cards sell back at a fraction of their price).

Singles: the stock is drawn from the card pool - cards whose tier is inside the town's region band (towns.json), whose colours fit
the town's (every colour of the card is one of the town's, or the card is colourless), never a basic land (they are sold on their
own, below), a banned or restricted card, or a name starting with a skipped prefix (numbers.json shop_skip_prefixes: "A-", Arena's
rebalanced versions).
numbers.json stock says how many slots of each tier, stock_copies how many copies a slot holds. The stock is drawn again every time
the shop opens, from the save's stable_rng(town, restock number), so it is the same until the town restocks - after every
restock_after_duels duels the player has fought anywhere. What was bought is kept in the save per town (shops[town]["bought"]),
and forgotten at a restock.

Basic lands (Round RPG7, Karl 13 Oct: "1 gold each"): while numbers.json says basic_lands_free is false, every town's shop sells
the six plain basic lands (Plains, Island, Swamp, Mountain, Forest, Wastes - the ones the card pool has; snow-covered ones come only
in packs) at basic_land_price each, as many as you can pay for, and buys none back (sell_price: tier 0 has no price). A new game's
starting deck comes with its basic lands owned (rpg.newgame adds every card of it to the collection).

Packs: the town's sets that have a pack pool (rpg/data/packs/<SET>.json, opened by Forge's own generator at build time); one pack
drawn from the pool with the save's rng, every card added to the collection.

Every purchase and sale changes the SaveGame in memory; the caller writes it (rpg.save.write) when the screen closes or at once.
"""
from rpg import cardpool, products, rules
from rpg.collection import sell_problem

_CANDIDATES = {}
BASIC_LANDS = ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")     # Round RPG7: what every shop sells, in this order


class Offer:
    __slots__ = ("slot", "name", "tier", "price", "copies", "left")

    def __init__(self, slot, name, tier, price, copies, left):
        self.slot, self.name, self.tier, self.price, self.copies, self.left = slot, name, tier, price, copies, left

    def __repr__(self):
        return f"Offer({self.slot!r}, {self.name!r}, tier {self.tier}, {self.price} gold, {self.left}/{self.copies} left)"


class ShopError(Exception):
    """A purchase or sale that can't be made; str() is the sentence for the screen."""


def fits(card, colours):
    if not colours:
        return True
    return card.colours == "C" or all(c in colours for c in card.colours)


def candidates(tier, colours, pool, tier_table, lists, skip=()):
    """Sorted names of the cards a shop may stock at `tier` for a town of `colours` (cached per pool)."""
    key = (id(pool), id(tier_table), id(lists), tier, colours, tuple(skip))
    if key not in _CANDIDATES:
        out = []
        for c in pool.cards.values():
            if c.is_basic or any(c.name.startswith(p) for p in skip) or not fits(c, colours):
                continue
            if lists.is_banned(c.name) or lists.is_restricted(c.name):
                continue
            if tier_table.tier_of(c.name) == tier:
                out.append(c.name)
        _CANDIDATES[key] = sorted(out)
    return _CANDIDATES[key]


class Shop:
    def __init__(self, save, town_id, world, numbers, pool=None, tier_table=None, lists=None):
        self.save = save
        self.town = world.town(town_id)
        self.band = world.band(self.town.region)
        self.numbers = numbers
        self.pool = pool or cardpool.load()
        if tier_table is None:
            from rpg import tiers
            tier_table = tiers.load(pool=self.pool)
        self.tiers = tier_table
        self.lists = lists or rules.vintage_lists()

    # ---- singles -------------------------------------------------------------------------------------------------------------

    def restock_number(self):
        return self.save.record["duels"] // self.numbers.restock_after_duels

    def _state(self):
        n = self.restock_number()
        st = self.save.shops.get(self.town.id)
        if st is None or st.get("restock") != n:
            st = {"restock": n, "bought": {}}
            self.save.shops[self.town.id] = st
        return st

    def singles(self):
        """[Offer] - this town's stock as it stands, cheapest first, then by name."""
        bought = self._state()["bought"]
        rng = self.save.stable_rng("shop", self.town.id, self.restock_number())
        lo, hi = self.band
        offers, taken = [], set()
        for tier in sorted(self.numbers.stock):
            if not lo <= tier <= hi:
                continue
            names = [n for n in candidates(tier, self.town.colours, self.pool, self.tiers, self.lists,
                                           self.numbers.shop_skip_prefixes) if n not in taken]
            for name in rng.sample(names, min(self.numbers.stock[tier], len(names))):
                taken.add(name)
                slot = str(len(offers))
                copies = rng.randint(*self.numbers.stock_copies)
                offers.append(Offer(slot, name, tier, self.numbers.price(tier), copies, max(0, copies - bought.get(slot, 0))))
        return sorted(offers, key=lambda o: (o.price, o.name.lower()))

    def buy_single(self, slot):
        """Buy one copy from a slot. Returns the Offer as it is after the purchase."""
        offer = next((o for o in self.singles() if o.slot == str(slot)), None)
        if offer is None:
            raise ShopError("That card isn't in this shop.")
        if offer.left < 1:
            raise ShopError(f"{offer.name} is sold out.")
        if self.save.gold < offer.price:
            raise ShopError(f"{offer.name} costs {offer.price} gold; you have {self.save.gold}.")
        self.save.gold -= offer.price
        self.save.collection.add(offer.name, 1)
        bought = self._state()["bought"]
        bought[offer.slot] = bought.get(offer.slot, 0) + 1
        offer.left -= 1
        return offer

    # ---- basic lands (Round RPG7) -------------------------------------------------------------------------------------------

    def basics(self):
        """[(name, price)] - the basic lands this shop sells; [] when the save's basic lands are free (nothing to buy). The save's
        rule decides, not numbers.json's (a save keeps the rule it was made under); the price is numbers.json's (0 if it says
        free)."""
        if self.save.collection.basics_free:
            return []
        out = []
        for name in BASIC_LANDS:
            card = self.pool.get(name)
            if card is not None and card.is_basic:
                out.append((card.name, self.numbers.basic_land_price))
        return out

    def buy_basic(self, name, k=1):
        """Buy k basic lands of one name. Returns the gold paid."""
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ShopError("Buy at least one (a whole number).")
        offer = next(((n, p) for n, p in self.basics() if n.lower() == str(name).strip().lower()), None)
        if offer is None:
            if self.save.collection.basics_free:
                raise ShopError("Basic lands are free - there is nothing to buy.")
            raise ShopError(f"{name} isn't a basic land this shop sells.")
        n, price = offer
        cost = price * k
        if self.save.gold < cost:
            raise ShopError(f"{k} {n} cost{'s' if k == 1 else ''} {cost} gold; you have {self.save.gold}.")
        self.save.gold -= cost
        self.save.collection.add(n, k)
        return cost

    # ---- packs ---------------------------------------------------------------------------------------------------------------

    def packs(self):
        """[(set code, set name, price)] - the town's sets that have a pack pool."""
        out = []
        for code in self.town.sets:
            if products.has_packs(code):
                out.append((code, products.read_pool(code).get("name", code), self.numbers.pack_price))
        return out

    def buy_pack(self, code):
        """Buy and open one pack: [{"name", "rarity", "foil", "new", "free"}] in the pack's order, every card added."""
        if code not in [c for c, _n, _p in self.packs()]:
            raise ShopError("This shop doesn't sell that pack.")
        price = self.numbers.pack_price
        if self.save.gold < price:
            raise ShopError(f"A pack costs {price} gold; you have {self.save.gold}.")
        cards = products.open_pack(code, self.save.rng("pack:" + code))
        self.save.gold -= price
        out = []
        for name, rarity, foil in cards:
            card = self.pool.get(name)
            shown = card.name if card is not None else name
            free = self.save.collection.is_free(shown)
            new = not free and self.save.collection.count(shown) == 0
            self.save.collection.add(shown, 1)
            out.append({"name": shown, "rarity": rarity, "foil": foil, "new": new, "free": free})
        return out

    # ---- selling -------------------------------------------------------------------------------------------------------------

    def sell_price(self, name):
        return self.numbers.sell_price(self.tiers.tier_of(name))

    def sell(self, name, k=1):
        """Sell k copies; returns the gold received."""
        why = sell_problem(name, k, self.save.collection, self.save.decks)
        if why:
            raise ShopError(why)
        each = self.sell_price(name)
        if each < 1:
            raise ShopError(f"No shop buys {name}.")
        self.save.collection.remove(name, k)
        self.save.gold += each * k
        return each * k
