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

Round PACK1 (Karl, 3 Nov 2026: "Add more options for purchasable packs"): besides a set's plain pack the shelf has
  - its premium pack (rpg/data/packs/premium/<SET>.json: the set's own template with 3 commons fewer, 2 uncommons and an extra
    rare or mythic, opened by Forge at build time);
  - a bundle of 3 plain packs of one set at once (cheaper than three);
  - Jumpstart theme packs: jumpstart_stock half-decks (20 cards, one theme) of Jumpstart, Jumpstart 2022 and 2025 whose colours are
    all the town's (a colourless one never - J22's Urza's tron deck alone sells back for 930 gold), drawn at each restock like the
    singles (stable_rng("jumpstart", town, restock number)), different themes, one of each; a bought one is gone until the next
    restock (shops[town]["themes"]: the codes bought).
Each kind opens with its own generator ("pack:<code>" as before for the plain pack, "pack:premium:<code>", "pack:bundle:<code>"),
so the packs an older save will open are the same as before this round. price_problems() is the check that every kind costs more
than its cards sell back for on average (claude/PACK1_2026-11-03.md section 4).

Every purchase and sale changes the SaveGame in memory; the caller writes it (rpg.save.write) when the screen closes or at once.
"""
import random

from rpg import progress
from rpg import cardpool, products, rules
from rpg.collection import deck_takes, sell_problem, take_out

_CANDIDATES = {}
_THEMES = {}                     # Round PACK2: theme_candidates per (pool, town colours, half-decks)
BASIC_LANDS = ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")     # Round RPG7: what every shop sells, in this order


BUNDLE_SAMPLE_SEED = 20261103                # Round PACK1: pack_values' sample of bundles
BUNDLE_PACKS = 3                             # Round PACK1: plain packs in a bundle


def fits_theme(half_colours, town_colours):
    """Round PACK1: a theme pack fits a town when it has a colour and every colour is the town's."""
    return bool(half_colours) and all(c in town_colours for c in half_colours)


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
        self.taken = []                 # Round SELL1: [(deck name, copies)] the last sale took out of saved decks

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

    def premium_packs(self):
        """Round PACK1: [(set code, set name, price)] - the town's sets that have a premium pool."""
        out = []
        for code, name, _p in self.packs():
            if products.has_premium(code):
                out.append((code, name, self.numbers.premium_pack_price))
        return out

    def bundles(self):
        """Round PACK1: [(set code, set name, price)] - a bundle of BUNDLE_PACKS plain packs of each set."""
        return [(code, name, self.numbers.bundle_price) for code, name, _p in self.packs()]

    def theme_candidates(self):
        """Round PACK1: the Jumpstart half-decks this town may stock - every card in the card pool, at least one colour, every
        colour the town's. Round PACK2: kept per (card pool, town colours) - the shelf asks every frame, and the answer only
        changes with those (it was most of the packs tab's frame time)."""
        key = (id(self.pool), self.town.colours, id(products.jumpstart()))
        hit = _THEMES.get(key)
        if hit is not None:
            return list(hit)
        out = []
        for h in products.jumpstart():
            if not all(self.pool.get(n) is not None for n, _k in h.cards):
                continue
            cols = products.colours(h, self.pool)
            if cols and fits_theme(cols, self.town.colours):
                out.append(h)
        _THEMES[key] = tuple(out)
        return out

    def theme_stock(self):
        """Round PACK1: [HalfDeck] the shop stocks this restock (bought ones included), different themes, in a stable order."""
        rng = self.save.stable_rng("jumpstart", self.town.id, self.restock_number())
        cands = sorted(self.theme_candidates(), key=lambda h: h.code)
        rng.shuffle(cands)
        out, themes = [], set()
        for h in cands:
            if h.theme in themes:
                continue
            out.append(h)
            themes.add(h.theme)
            if len(out) == self.numbers.jumpstart_stock:
                break
        return out

    def themes(self):
        """Round PACK1: [(half-deck, price)] still on the shelf (a bought one is gone until the next restock)."""
        bought = set(self._state().get("themes") or [])     # Round PACK1: absent in an older save, or before a first buy
        return [(h, self.numbers.jumpstart_price) for h in self.theme_stock() if h.code not in bought]

    def shelf(self):
        """Round PACK1: every pack the shop sells, as the packs tab lists them: [(kind, code, price)] - theme packs first, then for
        each set its plain pack, premium pack and bundle."""
        out = [("theme", h.code, price) for h, price in self.themes()]
        prem = {c for c, _n, _p in self.premium_packs()}
        for code, _name, price in self.packs():
            out.append(("plain", code, price))
            if code in prem:
                out.append(("premium", code, self.numbers.premium_pack_price))
            out.append(("bundle", code, self.numbers.bundle_price))
        return out

    def _pay(self, price, what):
        if self.save.gold < price:
            raise ShopError(f"{what} costs {price} gold; you have {self.save.gold}.")
        self.save.gold -= price

    def _add(self, cards, pack=None):
        out = []
        for name, rarity, foil in cards:
            card = self.pool.get(name)
            shown = card.name if card is not None else name
            free = self.save.collection.is_free(shown)
            new = progress.is_new(self.save, shown)      # Round PROG1: the one NEW rule (never owned before)
            self.save.collection.add(shown, 1)
            d = {"name": shown, "rarity": rarity, "foil": foil, "new": new, "free": free}
            if pack is not None:
                d["pack"] = pack                         # Round PACK1: which pack of a bundle (0, 1, 2)
            out.append(d)
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
        return self._add(cards)

    def buy_premium(self, code):
        """Round PACK1: buy and open a premium pack of `code`."""
        if code not in [c for c, _n, _p in self.premium_packs()]:
            raise ShopError("This shop doesn't sell that premium pack.")
        self._pay(self.numbers.premium_pack_price, "A premium pack")
        return self._add(products.open_premium(code, self.save.rng("pack:premium:" + code)))

    def buy_bundle(self, code):
        """Round PACK1: buy BUNDLE_PACKS plain packs of `code` at once; each card dict says which pack it came from ("pack")."""
        if code not in [c for c, _n, _p in self.packs()]:
            raise ShopError("This shop doesn't sell that bundle.")
        self._pay(self.numbers.bundle_price, f"A bundle of {BUNDLE_PACKS} packs")
        out = []
        for i, cards in enumerate(products.open_bundle(code, self.save.rng("pack:bundle:" + code), BUNDLE_PACKS)):
            out += self._add(cards, pack=i)
        return out

    def buy_theme(self, code):
        """Round PACK1: buy a Jumpstart theme pack (half-deck code) on the shelf: its cards in Forge's order, every one added."""
        half = next((h for h, _p in self.themes() if h.code == code), None)
        if half is None:
            if any(h.code == code for h in self.theme_stock()):
                raise ShopError("That theme pack is sold out until the shop restocks.")
            raise ShopError("This shop doesn't sell that theme pack.")
        self._pay(self.numbers.jumpstart_price, "A theme pack")
        st = self._state()
        if not isinstance(st.get("themes"), list):
            st["themes"] = []
        st["themes"].append(half.code)
        cards = []
        for name, k in half.cards:
            cards += [(name, "", False)] * k
        return self._add(cards)

    # ---- selling -------------------------------------------------------------------------------------------------------------

    def sell_price(self, name):
        return self.numbers.sell_price(self.tiers.tier_of(name))

    def sell(self, name, k=1):
        """Sell k copies; returns the gold received. Round SELL1: copies a saved deck uses that you'd no longer own come out of that
        deck (self.taken: [(deck name, copies)] from the last sale)."""
        why = sell_problem(name, k, self.save.collection, self.save.decks)
        if why:
            raise ShopError(why)
        each = self.sell_price(name)
        if each < 1:
            raise ShopError(f"No shop buys {name}.")
        takes = deck_takes(name, k, self.save.collection, self.save.decks)
        take_out(takes, name)
        taken = {}
        for d, _part, n in takes:
            taken[d.get("name", "?")] = taken.get(d.get("name", "?"), 0) + n
        self.taken = sorted(taken.items())
        self.save.collection.remove(name, k)
        self.save.gold += each * k
        return each * k


# ---- Round PACK1: what each kind of pack sells back for -----------------------------------------------------------------------

def sell_back(cards, sell_price):
    """The gold the cards [(name, ...)] sell back for, every copy (basic lands 0)."""
    return sum(sell_price(c[0]) for c in cards)


def pack_values(numbers, pool=None, tier_table=None, towns=None):
    """{(kind, where): [gold a pack's cards sell back for, one per pack in the pool]} for every kind of pack a shop sells: plain,
    premium and bundle per set (a bundle = BUNDLE_PACKS plain packs drawn from the plain pool: as many bundles as the pool has packs,
    drawn with a fixed seed), theme per town (the half-decks it may stock)."""
    from rpg import tiers as _tiers
    pool = pool or cardpool.load()
    tier_table = tier_table or _tiers.load(pool=pool)

    def each(name):
        card = pool.get(name)
        return numbers.sell_price(tier_table.tier_of(card.name if card is not None else name))

    out = {}
    for town in (towns.values() if isinstance(towns, dict) else towns or []):
        for code in town.sets:
            if products.has_packs(code) and ("plain", code) not in out:
                plain = [sell_back(p, each) for p in products.read_pool(code)["packs"]]
                out[("plain", code)] = plain
                rng = random.Random(BUNDLE_SAMPLE_SEED)             # 200 bundles of BUNDLE_PACKS packs drawn from the pool
                out[("bundle", code)] = [sum(rng.choice(plain) for _ in range(BUNDLE_PACKS)) for _ in plain]
                if products.has_premium(code):
                    out[("premium", code)] = [sell_back(p, each) for p in products.read_premium(code)["packs"]]
        vals = []
        for h in products.jumpstart():
            if all(pool.get(n) is not None for n, _k in h.cards):
                cols = products.colours(h, pool)
                if cols and fits_theme(cols, town.colours):
                    vals.append(sum(each(n) * k for n, k in h.cards))
        out[("theme", town.id)] = vals
    return out


PRICE_KEY = {"plain": "pack_price", "premium": "premium_pack_price", "bundle": "bundle_price", "theme": "jumpstart_price"}
MUST_BEAT = ("theme", "premium")             # Round PACK1 brief item 5: these must cost more than their mean sell-back


def price_problems(numbers, values):
    """Round PACK1: [sentence] for every kind in MUST_BEAT whose price is not above its mean sell-back somewhere."""
    out = []
    for (kind, where), vals in sorted(values.items()):
        if kind not in MUST_BEAT or not vals:
            continue
        mean = sum(vals) / len(vals)
        price = getattr(numbers, PRICE_KEY[kind])
        if price <= mean:
            out.append(f"{PRICE_KEY[kind]} {price} is not above the {mean:.1f} gold a {kind} pack ({where}) sells back for")
    return out
