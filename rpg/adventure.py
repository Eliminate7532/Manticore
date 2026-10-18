# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's glue between its screens and the RPG's logic (Round RPG8, Karl 13 Oct 2026: "First playable loop" - main menu >
Adventure > new game > walk the map > duel an enemy on the real table > gold and cards > the town's shop > the deck editor >
save). No pygame: rpg_screens.py draws; everything it decides is here, where a test can reach it.

    slot_rows(folder)              what the slot screen lists (rpg.save.slots for numbers.json's save_slots)
    Game.load(slot, folder)        an opened save, and the notes to show (a backup used, a duel that didn't finish)
    Game.create(flow, slot, ...)   a new game from a finished NewGameFlow, written at once
    Game.write()                   the save, written safely (rpg.save.write)
    fight_text(game, trigger)      the lines the fight question shows (who, what a win gives, what a loss costs)
    start_duel(game, trigger)      rpg.duel.start for a foe walked into on the map; the save is written with the duel marked as
                                   going on BEFORE the game starts (rpg.duel's rule), so a duel the program never comes back from
                                   is known at the next load
    finish_duel(slot, result, ..)  the game's result applied to the save on disk and written; "unfinished" leaves the duel marked
                                   (the next load says it didn't count - rpg.duel.settle_pending)
    outcome_lines(out, world)      what the end screen and the map say about it
    DeckEditor                     the deck editor's model: the collection on one side, the deck on the other

Choices of mine (Karl's to change; each is one place):
  - A won, lost, conceded or drawn duel is settled the moment the game knows it (the table's game record, stats.py), not when you
    press Continue: a crash after the game ended can't lose a reward or a loss.
  - The deck editor changes the deck in memory and writes it when you leave the editor; a deck that breaks the rules is kept as
    it is (you may be in the middle of building it) - only a duel refuses it (rpg.duel.start says why).
  - The sideboard isn't edited here yet (it is kept as it is; the rules still count it).
"""
from rpg import cardpool, duel, economy, enemies, rules
from rpg import save as rsave
from rpg.collection import deck_counts
from rpg.collection import problems as deck_problems

ENCOUNTER_WORDS = {"regular": "", "dungeon": "a dungeon's guardian", "boss": "a boss"}
TYPE_ORDER = ("Creature", "Planeswalker", "Instant", "Sorcery", "Artifact", "Enchantment", "Battle", "Land", "Other")
COLOURS = "WUBRGC"
DECK_NAME = "Starting deck"


def numbers():
    return economy.read_numbers()


def world():
    return economy.read_world()


def slot_rows(folder=None, n=None):
    """[{"slot", "used", "name", "gold", "updated", "wins", "problem"}] for every save slot (numbers.json save_slots)."""
    return rsave.slots(n or numbers().save_slots, folder)


def kind_names():
    """{enemy kind id: its name} ("grave_robber": "Grave Robber")."""
    try:
        return {k["id"]: k.get("name") or k["id"] for k in enemies.read_kinds()}
    except (OSError, ValueError, KeyError):
        return {}


def enemy_name(kind_id):
    return kind_names().get(kind_id) or (kind_id or "?").replace("_", " ").title()


class Game:
    """One opened Adventure: its slot, its save, and the data the screens need (read once)."""

    def __init__(self, slot, save, folder=None, pool=None, lists=None, numbers_=None, world_=None):
        self.slot, self.save, self.folder = slot, save, folder
        self.pool = pool if pool is not None else (save.collection.pool or cardpool.load())
        self.save.collection.pool = self.pool
        self._lists = lists
        self.numbers = numbers_ or numbers()
        self.world = world_ or world()
        self._tiers = None

    # data, read when first needed
    def lists(self):
        if self._lists is None:
            self._lists = rules.vintage_lists()
        return self._lists

    def tiers(self):
        if self._tiers is None:
            from rpg import tiers
            self._tiers = tiers.load(pool=self.pool, lists=self.lists())
        return self._tiers

    # opening and writing
    @classmethod
    def load_raw(cls, slot, folder=None, pool=None, lists=None):
        """(Game, [notes]) without settling a duel left going on (finish_duel is the one that settles it). SaveError /
        FileNotFoundError when the slot can't be opened."""
        pool = pool if pool is not None else cardpool.load()
        s, note = rsave.read(slot, folder, pool=pool)
        return cls(slot, s, folder, pool=pool, lists=lists), ([note] if note else [])

    @classmethod
    def load(cls, slot, folder=None, pool=None, lists=None):
        """(Game, [notes]). A backup that had to be used, and a duel left going on (it doesn't count), are notes; the second is
        settled here and written back at once. SaveError / FileNotFoundError when the slot can't be opened."""
        game, notes = cls.load_raw(slot, folder, pool, lists)
        pending = duel.settle_pending(game.save)
        if pending:
            notes.append(pending)
            game.write()
        return game, notes

    @classmethod
    def create(cls, flow, slot, folder=None, forge=""):
        """A new game from a finished rpg.newgame.NewGameFlow, written to its slot (an old save there goes to its .bak first -
        rpg.save.write keeps a readable old file as the backup)."""
        s = flow.finish(slot, forge=forge)
        game = cls(slot, s, folder, pool=flow._pool(), lists=flow._lists(), numbers_=flow.numbers, world_=flow.world)
        game.write()
        return game

    def write(self):
        return rsave.write(self.save, self.folder)

    # what the screens show
    def deck(self):
        return self.save.deck()

    def deck_size(self):
        d = self.save.deck()
        return sum(k for _n, k in d["main"]) if d else 0

    def deck_problems(self):
        d = self.save.deck()
        if d is None:
            return [rules.Problem("none", "You have no deck.")]
        return deck_problems(d, self.save.collection, self.pool, self.lists())

    def town_name(self, town_id):
        try:
            return self.world.town(town_id).name
        except KeyError:
            return town_id or "?"

    def panel_info(self):
        """The map's side panel: gold, the deck and its size."""
        d = self.save.deck()
        return {"gold": self.save.gold, "deck": d["name"] if d else "No deck", "cards": self.deck_size()}


# ---- duels ------------------------------------------------------------------------------------------------------------------

def fight_text(game, trigger):
    """(title, [lines]) for the question asked when you walk into a foe."""
    name = enemy_name(trigger.enemy)
    what = ENCOUNTER_WORDS.get(trigger.encounter, "")
    title = f"{name}" + (f" - {what}" if what else "")
    n = game.numbers
    lo, hi = n.rewards[trigger.encounter]["gold"]
    cards = n.reward_cards(trigger.encounter)
    gold = f"{lo} gold" if lo == hi else f"{lo}-{hi} gold"
    win = f"Win: {gold}" + (f" and {cards} card{'s' if cards != 1 else ''}" if cards else "") + "."
    town = game.town_name(game.save.world.get("last_town") or game.world.start_town)
    cost = n.loss_cost(game.save.gold)
    lose = (f"Lose: {cost} of your {game.save.gold} gold, and back to {town}." if cost
            else f"Lose: back to {town}.")
    lines = [win, lose]
    if not trigger.once:
        lines.append("Beaten, it comes back after your next visit to a town.")
    wrong = game.deck_problems()
    if wrong:
        lines.append("Your deck can't be played yet: " + wrong[0].text)
    return title, lines


def start_duel(game, trigger, now=None):
    """Mark the duel as going on and write the save; returns the rpg.duel.Duel. DuelError (a sentence) when it can't start."""
    d = duel.start(game.save, trigger.enemy, trigger.encounter, pool=game.pool, lists=game.lists(), now=now, spot=trigger.spot,
                   once=trigger.once)
    d.enemy_name = enemy_name(trigger.enemy) or d.enemy_name
    try:
        game.write()
    except OSError:
        game.save.pending_duel = None           # not written: not going on (else every later Fight says one is)
        raise
    return d


def cancel_duel(game):
    """The duel never started (the engine couldn't be started): nothing is won or lost, the mark is taken off and written."""
    if game.save.pending_duel is not None:
        game.save.pending_duel = None
        game.write()


def duel_stats_dir(folder=None):
    """Where the table's game record (stats.py) keeps the Adventure's duels: rpg_saves/duel_stats/, apart from the record of
    your other games (the main Stats page) - the collection and its decks live only in the Adventure (Karl, 11 Oct)."""
    import os
    return os.path.join(folder or rsave.saves_dir(), "duel_stats")


def deck_cards(pairs):
    """[(name, count)] -> the list of names the table's launcher takes (repeats for copies)."""
    out = []
    for n, k in pairs:
        out += [n] * int(k)
    return out


def finish_duel(slot, result, folder=None, pool=None):
    """Apply a duel's result to the save of `slot` (read fresh from the disk) and write it. Returns rpg.duel.finish's dict, or
    None when there was nothing to settle (no duel going on, or result "unfinished": the mark stays, and the next load says the
    duel didn't count). SaveError / DuelError go to the caller."""
    if result not in duel.RESULTS:
        raise duel.DuelError(f"Unknown result {result!r}.")
    if result == "unfinished":
        return None
    game, _notes = Game.load_raw(slot, folder, pool)
    return finish_loaded(game, result)


def finish_loaded(game, result):
    """finish_duel on a save already open (the Adventure window tries a result again this way). None when no duel is going on.
    The save is written; when that fails the change is undone in memory too (OSError to the caller)."""
    if result == "unfinished" or game.save.pending_duel is None:
        return None
    before = game.save.to_dict()
    kind = game.save.pending_duel.get("kind")
    out = duel.finish(game.save, result, game.numbers, game.world, pool=game.pool, tier_table=game.tiers(), lists=game.lists())
    out["enemy"] = enemy_name(kind)
    try:
        game.write()
    except OSError:
        back = rsave.SaveGame.from_dict(before, pool=game.pool)            # not written: as it was (the duel still marked),
        game.save.__dict__.update(back.__dict__)                            # in the same object the screens hold
        raise
    return out


def outcome_lines(out, world_=None):
    """The lines that say what a settled duel did ([] for None)."""
    if not out:
        return []
    enemy = out.get("enemy") or "the enemy"
    r = out.get("result")
    if r == "won":
        lines = [f"You beat the {enemy}: +{out.get('gold', 0)} gold."]
        cards = [n for n, _src in out.get("cards") or []]
        if cards:
            lines.append(f"New card{'s' if len(cards) != 1 else ''}: " + ", ".join(cards) + ".")
        return lines
    if r in ("lost", "conceded"):
        town = out.get("moved_to")
        if town:
            try:                                 # the town's name, never its id (the end screen passes no world: it is read here)
                town = (world_ if world_ is not None else world()).town(town).name
            except (KeyError, OSError, ValueError, TypeError, AttributeError):
                pass
        lost = -int(out.get("gold") or 0)
        first = f"You conceded to the {enemy}." if r == "conceded" else f"The {enemy} beat you."
        second = (f"You lost {lost} gold" if lost else "You had no gold to lose") + (f" and are back in {town}." if town else ".")
        return [first, second]
    if r == "draw":
        return [f"A draw with the {enemy}: nothing won or lost."]
    return []


# ---- the deck editor ----------------------------------------------------------------------------------------------------------

def type_group(card):
    """The column a card goes in ("Creature", ..., "Land", "Other"), from its type line."""
    t = (card.type_line if card is not None else "") or ""
    if "Creature" in t:
        return "Creature"
    if "Land" in t:
        return "Land"
    for k in ("Planeswalker", "Instant", "Sorcery", "Artifact", "Enchantment", "Battle"):
        if k in t:
            return k
    return "Other"


def copy_limit(card, lists):
    """How many copies a deck may hold (main and sideboard together): None = any number."""
    if card is None:
        return 0
    if lists.is_banned(card.name):
        return 0
    if lists.is_restricted(card.name):
        return 1
    if card.is_basic or card.copy_limit < 0:
        return None
    return card.copy_limit if card.copy_limit > 0 else rules.FOUR


class Row:
    __slots__ = ("name", "card", "owned", "used", "group")

    def __init__(self, name, card, owned, used, group):
        self.name, self.card, self.owned, self.used, self.group = name, card, owned, used, group

    def __repr__(self):
        return f"Row({self.name!r}, owned {self.owned}, in deck {self.used})"


class DeckEditor:
    """The deck being edited (its main deck as {name: count}), the collection it is built from, filters, and the rules."""

    def __init__(self, game, index=None):
        self.game = game
        s = game.save
        if not s.decks:
            s.decks.append({"name": DECK_NAME, "main": [], "sideboard": []})
            s.active_deck = 0
        if index is None:
            index = s.active_deck if s.active_deck is not None else 0
        self.index = index
        d = s.decks[self.index]
        self.name = d["name"]
        self.main = {}
        for n, k in d["main"]:
            self.main[self._shown(n)] = self.main.get(self._shown(n), 0) + int(k)
        self.sideboard = [list(e) for e in d.get("sideboard", [])]
        self.colours = set()            # letters of COLOURS; empty = every colour
        self.group = None               # one of TYPE_ORDER, or None = every type
        self.search = ""
        self.changed = False

    def _shown(self, name):
        card = self.game.pool.get(name)
        return card.name if card is not None else name

    # counts
    def owned(self, name):
        n = self.game.save.collection.count(name)
        return 10 ** 6 if n is None else n

    def in_deck(self, name):
        return self.main.get(self._shown(name), 0)

    def in_sideboard(self, name):
        key = cardpool.normal_name(self._shown(name))
        return sum(int(k) for n, k in self.sideboard if cardpool.normal_name(n) == key)

    def total(self):
        return sum(self.main.values())

    def why_not(self, name):
        """Why one more copy can't go in (a sentence), or None."""
        card = self.game.pool.get(name)
        shown = self._shown(name)
        if card is None:
            return f"{shown} isn't a card Forge can play."
        limit = copy_limit(card, self.game.lists())
        have = self.in_deck(shown) + self.in_sideboard(shown)
        if limit == 0:
            return f"{shown} is banned."
        if limit is not None and have >= limit:
            return f"A deck may hold {'one' if limit == 1 else limit} {shown}" + (" (restricted)." if limit == 1 else ".")
        if self.in_deck(shown) + self.in_sideboard(shown) >= self.owned(shown):
            return f"You own {self.owned(shown)} {shown}, and the deck already uses {'it' if self.owned(shown) == 1 else 'them all'}."
        return None

    def add(self, name):
        if self.why_not(name):
            return False
        shown = self._shown(name)
        self.main[shown] = self.main.get(shown, 0) + 1
        self.changed = True
        return True

    def remove(self, name):
        shown = self._shown(name)
        if self.main.get(shown, 0) < 1:
            return False
        self.main[shown] -= 1
        if not self.main[shown]:
            del self.main[shown]
        self.changed = True
        return True

    # filters
    def toggle_colour(self, letter):
        self.colours ^= {letter}

    def set_group(self, group):
        self.group = None if group == self.group else group

    def matches(self, card, name):
        if self.colours and not any(c in self.colours for c in (card.colours if card is not None else "C")):
            return False
        if self.group and type_group(card) != self.group:
            return False
        if self.search:
            words = cardpool.normal_name(self.search).split()
            hay = cardpool.normal_name(name + " " + (card.type_line if card is not None else ""))
            if not all(w in hay for w in words):
                return False
        return True

    def collection_rows(self):
        """Every card you own (and every card the deck holds), filtered, by mana value then name."""
        coll = self.game.save.collection
        names = {self._shown(n) for n in coll.names()} | set(self.main)
        rows = []
        for name in names:
            card = self.game.pool.get(name)
            if not self.matches(card, name):
                continue
            owned = coll.count(name)
            rows.append(Row(name, card, owned, self.in_deck(name), type_group(card)))
        return sorted(rows, key=lambda r: (r.group == "Land", r.card.mana_value if r.card else 99, r.name.lower()))

    def deck_rows(self):
        """[(group, [(name, count, card)])] - the deck by type, each by mana value then name."""
        groups = {}
        for name, k in self.main.items():
            card = self.game.pool.get(name)
            groups.setdefault(type_group(card), []).append((name, k, card))
        out = []
        for g in TYPE_ORDER:
            if g in groups:
                out.append((g, sorted(groups[g], key=lambda e: (e[2].mana_value if e[2] else 99, e[0].lower()))))
        return out

    def as_deck(self):
        return {"name": self.name, "main": [[n, k] for n, k in sorted(self.main.items(), key=lambda e: e[0].lower()) if k > 0],
                "sideboard": [list(e) for e in self.sideboard]}

    def problems(self):
        return deck_problems(self.as_deck(), self.game.save.collection, self.game.pool, self.game.lists())

    def apply(self):
        """Put the edited deck in the save (it becomes the deck you duel with) and write it. Returns the problems left."""
        s = self.game.save
        s.decks[self.index] = self.as_deck()
        s.active_deck = self.index
        self.game.write()
        self.changed = False
        return self.problems()


def deck_uses(game):
    """{normal name: copies the saved decks use at most} - what the sell list warns about."""
    out = {}
    for d in game.save.decks:
        for key, (_name, k) in deck_counts(d).items():
            out[key] = max(out.get(key, 0), k)
    return out
