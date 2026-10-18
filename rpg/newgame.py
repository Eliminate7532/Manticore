# SPDX-License-Identifier: GPL-3.0-or-later
"""Starting a new RPG game (RPG plan decision 11 and "Rules of play": "a name, then three of Forge's 363 Jumpstart half-decks,
which together make about 60 cards").

NewGameFlow is the new-game screen's logic without the screen: the steps are the name, then three picks. Each pick is offered a few
half-decks of different themes at a time (products.offer), with "show others" (reroll) as often as the player likes, and back
(undo the last pick). preview() is the deck the picks so far make. finish() makes the save: the starting deck (products.starter_deck:
four of a card at most, one of a restricted card, filled to 60 with its most common basic land), the collection (every card of that
deck - its basic lands too since Round RPG7, when they stopped being free; free ones are not counted), the starting gold, and the
world at the start town.

The offers come from the new game's own seed, so a test (or a bug report) can replay them exactly.

Round RPG9 (Karl, 14 Oct 2026: "Two colours max"): the three picks use at most MAX_COLOURS colours between them - offer() and
all_choices() leave out a half-deck that would make a third colour, and pick() refuses one with a sentence saying why. A half-deck's
colours are those of its non-land cards (products.colours). Karl's deck of 13 Oct (Angels, Beasts, Spirits: white, green and blue,
7 basic lands of each) is what this stops.
"""
import os
import time

from rpg import cardpool, economy, products, rules
from rpg.save import SaveError, SaveGame, check_name

OFFER_SIZE = 3            # half-decks shown at a time (my estimate: three cards side by side fit any window)
PICKS = 3                 # decision 11
MAX_COLOURS = 2           # Round RPG9 (Karl, 14 Oct 2026: "Two colours max"): the three picks together use at most two colours, so
                          # the deck's basic lands match its spells. JMP Rainbow and J25 Chaos (five colours each) can't be picked.


class NewGameFlow:
    def __init__(self, seed=None, decks=None, pool=None, lists=None, numbers=None, world=None, offer_size=OFFER_SIZE):
        self.seed = int.from_bytes(os.urandom(6), "big") if seed is None else int(seed)
        self._decks = decks
        self.pool = pool
        self.lists = lists
        self.numbers = numbers
        self.world = world
        self.offer_size = offer_size
        self.name = None
        self.picks = []                  # HalfDeck
        self.rerolls = 0
        self._offer = None

    # ---- data, loaded when first needed --------------------------------------------------------------------------------------

    def decks(self):
        if self._decks is None:
            self._decks = products.jumpstart()
        return self._decks

    def _pool(self):
        if self.pool is None:
            self.pool = cardpool.load()
        return self.pool

    def _lists(self):
        if self.lists is None:
            self.lists = rules.vintage_lists()
        return self.lists

    # ---- the steps -----------------------------------------------------------------------------------------------------------

    @property
    def step(self):
        """"name", "pick" or "done"."""
        if self.name is None:
            return "name"
        return "pick" if len(self.picks) < PICKS else "done"

    def set_name(self, name):
        """None when the name is taken, else the sentence saying what's wrong with it."""
        try:
            self.name = check_name(name)
        except SaveError as e:
            return str(e)
        return None

    def colours(self):
        """Round RPG9: the colours the picks so far use ("" before the first), in WUBRG order."""
        return products.colours_of(self.picks, self._pool())

    def allowed(self, half):
        """Round RPG9: whether `half` can be the next pick - with it, the picks use at most MAX_COLOURS colours."""
        return len(products.colours_of(self.picks + [half], self._pool())) <= MAX_COLOURS

    def colour_note(self):
        """Round RPG9: one line for the screen about the colour rule as it stands after the picks so far."""
        cols = self.colours()
        if not self.picks or not cols:
            return f"At most {MAX_COLOURS} colours in all."
        if len(cols) < MAX_COLOURS:
            return f"So far {products.colour_words(cols)}: one more colour at most."
        return f"Your colours: {products.colour_words(cols)}. Only half-decks of these colours are offered now."

    def offer(self):
        """The half-decks to choose from now (different themes, none already picked; Round RPG9: only those that keep the picks
        within MAX_COLOURS colours)."""
        if self.step != "pick":
            return []
        if self._offer is None:
            from rpg.save import _seed_int
            import random
            rng = random.Random(_seed_int(self.seed, "offer", len(self.picks), self.rerolls))
            free = [h for h in self.decks() if self.allowed(h)]
            self._offer = products.offer(rng, free, self.offer_size, exclude={h.code for h in self.picks})
        return list(self._offer)

    def reroll(self):
        """Show other half-decks for this pick."""
        self.rerolls += 1
        self._offer = None
        return self.offer()

    def all_choices(self):
        """Every half-deck still free to pick, by set, theme and number - for a screen that lets the player browse all 363
        instead of (or beside) the offer. Round RPG9: only those within the colour rule."""
        picked = {h.code for h in self.picks}
        return sorted((h for h in self.decks() if h.code not in picked and self.allowed(h)),
                      key=lambda h: (h.set, h.theme.lower(), h.number))

    def pick(self, code):
        """Take a half-deck by its code: one from the offer, or any other not picked yet (the browse list)."""
        if self.step != "pick":
            raise ValueError("nothing to pick now")
        for h in self.offer() + self.all_choices():
            if h.code == code:
                self.picks.append(h)
                self._offer = None
                return h
        for h in self.decks():
            if h.code == code and h.code not in {p.code for p in self.picks}:
                cols = products.colours_of(self.picks + [h], self._pool())
                raise ValueError(f"{h.theme} would make a deck of {len(cols)} colours ({products.colour_words(cols)}); "
                                 f"a starting deck has at most {MAX_COLOURS}.")
        raise ValueError(f"{code!r} isn't a half-deck that can be picked")

    def undo(self):
        """Back one step: the last pick, else the name."""
        self._offer = None
        if self.picks:
            return self.picks.pop()
        self.name = None
        return None

    def preview(self):
        """[(name, count)] - the deck the picks so far make (the full starting deck once three are picked)."""
        if not self.picks:
            return []
        return products.starter_deck(self.picks, self._pool(), self._lists())

    # ---- the save ------------------------------------------------------------------------------------------------------------

    def finish(self, slot, now=None, forge=""):
        """The new SaveGame (not written yet - rpg.save.write does that)."""
        if self.step != "done":
            raise ValueError(f"the new game isn't finished (step {self.step!r})")
        numbers = self.numbers or economy.read_numbers()
        world = self.world or economy.read_world()
        pool = self._pool()
        deck = self.preview()
        save = SaveGame(slot, self.name, self.seed, pool=pool, basics_free=numbers.basic_lands_free)
        save.created = save.updated = time.time() if now is None else now
        for n, k in deck:
            card = pool.get(n)
            save.collection.add(card.name if card is not None else n, k)
        save.decks = [{"name": "Starting deck", "main": [[(pool.get(n).name if pool.get(n) else n), k] for n, k in deck],
                       "sideboard": []}]
        save.active_deck = 0
        save.start = {"half_decks": [h.code for h in self.picks]}
        save.gold = numbers.start_gold
        town = world.start_town
        save.world = {"region": world.town(town).region, "map": town, "x": None, "y": None, "last_town": town,
                      "towns_visited": [town]}
        save.forge = forge or getattr(pool, "forge", "") or ""
        return save
