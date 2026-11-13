# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's glue between its screens and the RPG's logic (Round RPG8, Karl 13 Oct 2026: "First playable loop" - main menu >
Adventure > new game > walk the map > duel an enemy on the real table > gold and cards > the town's shop > the deck editor >
save). No pygame: rpg_screens.py draws; everything it decides is here, where a test can reach it.

    slot_rows(folder)              what the slot screen lists (rpg.save.slots for numbers.json's save_slots)
    Game.load(slot, folder)        an opened save, and the notes to show (a backup used, a duel that didn't finish, a save
                                   from before RPG12's maps moved to the inn)
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


def _rare_table():
    from rpg import rare
    try:
        return rare.spawns()
    except (OSError, ValueError):
        return {}


def rare_entry(trigger):
    """Round RPG16: the rare_spawns.json entry of a rare foe's trigger (one with a `rare` id), else None."""
    rid = getattr(trigger, "rare", None)
    e = _rare_table().get(rid) if isinstance(rid, str) else None
    return e if isinstance(e, dict) and e.get("name") else None


def deck_source(game, trigger):
    """Round RPG17: the deck list ("jumpstart" / "archetype") a rare with its own "decks" plays (rpg.rare.deck_encounter), or
    None - the duel's own rule (the region's list for the trigger's encounter)."""
    entry = rare_entry(trigger)
    if not entry or "decks" not in entry:
        return None
    from rpg import rare as rpg_rare
    return duel.region_source(game.save, rpg_rare.deck_encounter(trigger.rare, entry))


def reward_words(reward):
    """"40 gold" / "a rare card" / "40 gold and a rare card" for a reward dict (rpg.rare), "" for none."""
    reward = reward if isinstance(reward, dict) else {}      # Round RPG16d: a hand-edited reward that isn't a table says nothing
    parts = []
    if isinstance(reward.get("gold"), int) and reward["gold"] > 0:
        parts.append(f"{reward['gold']} gold")
    if reward.get("card") == "rare_from_band":
        parts.append("a rare card")
    return " and ".join(parts)


def enemy_name(kind_id):
    return kind_names().get(kind_id) or (kind_id or "?").replace("_", " ").title()


def foe_name(trigger):
    """Round RPG18b: what a foe is called - a rare's own name, else the map's title for it (the bandits' leader), else its kind's."""
    entry = rare_entry(trigger)
    if entry:
        return entry["name"]
    title = getattr(trigger, "title", None)
    return title if isinstance(title, str) and title else enemy_name(getattr(trigger, "enemy", None))


def _move_old_save(game):
    """Round RPG12: rpg.explore.move_old_save for an opened game; a map folder that can't be read moves nothing (the map screen
    says so when it opens)."""
    try:
        from rpg import explore
        return explore.move_old_save(game.save.world, game.world.start_town)
    except Exception:                                # a damaged map, PyTMX missing: the map screen says it
        return None


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
        moved = _move_old_save(game)                 # Round RPG12: a save from before the new maps goes on at the inn
        if moved:
            notes.append(moved)
        if pending or moved:
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
        out = {"gold": self.save.gold, "deck": d["name"] if d else "No deck", "cards": self.deck_size()}
        try:                                       # Round PROG3: "Level 3  XP 12/60" (never stops the panel)
            from rpg import levels
            out["level"] = levels.bar_text(levels.state(self.save)["xp"])
            out["perk"] = bool(levels.offers(self.save))
        except (OSError, ValueError):
            pass
        try:                                       # Round PROG4: the gear worn (the corner panel draws their icons)
            from rpg import gear
            out["gear"] = [(sl.id, p.id, p.icon, p.name) for sl, p in gear.worn(self.save)]
        except (OSError, ValueError):
            pass
        return out


# ---- duels ------------------------------------------------------------------------------------------------------------------

def inn_town(game, map_name):
    """Round RPG15b: the name of the town whose map the inn is on (the map's town property), or None."""
    try:
        from rpg import explore
        town = explore.load_area(map_name).town
        return game.town_name(town) if town else None
    except Exception:                                    # a map that won't load says nothing here; the loss itself still works
        return None


# Round GOLD1: the words about the gold a foe holds (Karl to edit; rpg_encounter draws HOLDS_LINE in gold)
HOLDS_LINE = "Holds {n} of your gold - win it back."
LOST_HELD_LINE = "You lost {n} gold - the {enemy} has it now. Beat them to win it back."
WON_BACK_LINE = "{n} of it won back from the {enemy}."


def fight_text(game, trigger):
    """(title, [lines]) for the question asked when you walk into a foe."""
    entry = rare_entry(trigger)                          # Round RPG16: a rare foe has its own name, reward and cooldown
    name = foe_name(trigger)                             # ... Round RPG18b: or the map's title for it
    what = ENCOUNTER_WORDS.get(trigger.encounter, "")
    title = f"{name}" + (f" - {what}" if what else "")
    n = game.numbers
    lo, hi = n.rewards[trigger.encounter]["gold"]
    cards = n.reward_cards(trigger.encounter)
    gold = f"{lo} gold" if lo == hi else f"{lo}-{hi} gold"
    win = f"Win: {gold}" + (f" and {cards} card{'s' if cards != 1 else ''}" if cards else "") + "."
    if entry:
        extra = reward_words(entry.get("reward"))
        if extra:
            win = win[:-1] + f", and {extra}."
    if getattr(trigger, "gauntlet", False):              # Round PROG5: the town gauntlet - its own win and loss
        from rpg import extras
        lines = [extras.GAUNTLET_WIN, extras.GAUNTLET_LOSE]
        try:
            g, ex = extras.gauntlet_state(game.save.world), extras.read()
            if g:
                lines.insert(0, extras.GAUNTLET_NEXT.format(n=g["played"] + 1, duels=ex.duels, wins=g["wins"]).rsplit(" ", 1)[0])
        except (OSError, ValueError):
            pass
        start = start_words(game)
        if start:
            lines.append(start)
        wrong = game.deck_problems()
        if wrong:
            lines.append("Your deck can't be played yet: " + wrong[0].text)
        return title, lines
    town = game.town_name(game.save.world.get("last_town") or game.world.start_town)
    where = f"back to {town}"
    inn = game.save.world.get("inn")                     # Round RPG15b: a loss wakes you at the inn you last rested at (rpg.duel)
    if isinstance(inn, dict) and inn.get("map"):
        where = f"you wake at {inn_town(game, inn['map'])}'s inn" if inn_town(game, inn["map"]) else "you wake at the inn"
    cost = duel.loss_cost(game.save, n, trigger.encounter)   # PROG3: a perk may take less; GOLD1: by kind of fight
    lose = (f"Lose: {cost} of your {game.save.gold} gold, and {where}." if cost
            else f"Lose: {where[0].upper() + where[1:]}.")
    lines = [win, lose]
    held = duel.held_by(game.save, getattr(trigger, "spot", None))   # Round GOLD1: what this foe took from you before
    if held:
        lines.append(HOLDS_LINE.format(n=held))
    if entry:
        from rpg import rare
        days = rare.cooldown_of(trigger.rare, entry)        # Round RPG16d (review T1): a hand-edited "three" never reaches :g
        lines.append(f"Beaten, it stays away for {days:g} day{'s' if days != 1 else ''}." if days else "Beaten, it may come back.")
    elif not trigger.once and not getattr(trigger, "random", False):   # Round RPG13: a random foe has no spot to come back to
        lines.append("Beaten, it comes back after your next visit to a town.")
    carry = gear_carried(game, trigger)                  # Round PROG4: the piece of gear this foe gives when beaten
    if carry:
        lines.append(carry)
    start = start_words(game)                            # Round PROG3: your levels and perks at the duel's start
    if start:
        lines.append(start)
    wrong = game.deck_problems()
    if wrong:
        lines.append("Your deck can't be played yet: " + wrong[0].text)
    return title, lines


def duel_effects(game):
    """Round PROG3: the player's start effects in this save's duels (rpg.levels.duel_effects), {} when progression.json can't be
    read - the duel as before, never a stopped fight."""
    from rpg import levels
    try:
        return levels.duel_effects(game.save)
    except (OSError, ValueError):
        return {}


def start_words(game):
    """Round PROG3: "You start with 23 life and a Treasure." for the encounter screen; "" at level 1 with no perks."""
    import start_effects
    try:
        return start_effects.describe(duel_effects(game), base_life=duel.LIFE)
    except start_effects.StartEffectsError:
        return ""


def gear_carried(game, trigger):
    """Round PROG4: "Carries: <piece> (gear)." on the encounter screen of a rare foe or a boss whose piece isn't owned yet, else ""."""
    from rpg import gear
    rid = getattr(trigger, "rare", None)
    if rid and rare_entry(trigger):
        return gear.carries(game.save, "rares", rid)
    if getattr(trigger, "encounter", None) == "boss":
        return gear.carries(game.save, "bosses", getattr(trigger, "enemy", None))
    return ""


def quest_gear(game, quest_id):
    """Round PROG4: the piece of gear a quest handed in gives (rpg/data/gear.json sources.quests). Returns the lines to say (the
    save is written by the caller, as for the reward)."""
    from rpg import gear
    return gear.drop_for(game.save, "quests", quest_id) if quest_id else []


def start_duel(game, trigger, now=None, index=None):
    """Mark the duel as going on and write the save; returns the rpg.duel.Duel. DuelError (a sentence) when it can't start.
    trigger: a map's encounter (rpg.explore.Trigger) or, Round RPG13, a random one (rpg.encounters.RandomFoe: no spot). index: the
    enemy deck the encounter screen showed (rpg.duel.peek), so the duel plays exactly that one."""
    d = duel.start(game.save, trigger.enemy, trigger.encounter, pool=game.pool, lists=game.lists(), now=now, spot=trigger.spot,
                   once=trigger.once, index=index, source=deck_source(game, trigger),      # RPG17: a rare's own deck lists
                   tier_table=game.tiers())                                                 # PROG2: a boss's signature card
    d.enemy_name = foe_name(trigger) or d.enemy_name     # Round RPG18b: the map's title (the bandits' leader) too
    d.effects = duel_effects(game) or None               # Round PROG3: your levels and perks (the table passes them to the bridge)
    d.life = (d.effects or {}).get("life", d.life)
    if getattr(trigger, "title", None) and isinstance(game.save.pending_duel, dict):
        game.save.pending_duel["name"] = trigger.title    # ... so the result names him
    if getattr(trigger, "gauntlet", False) and isinstance(game.save.pending_duel, dict):
        game.save.pending_duel["gauntlet"] = True         # Round PROG5: the town gauntlet's duel (rpg.extras)
    entry = rare_entry(trigger)
    if entry and isinstance(game.save.pending_duel, dict):          # Round RPG16: the result names it and pays its reward
        game.save.pending_duel["rare"] = trigger.rare
        d.enemy_name = entry["name"]
    try:
        game.write()
    except OSError:
        game.save.pending_duel = None           # not written: not going on (else every later Fight says one is)
        raise
    return d


def give_reward(game, reward):
    """Round RPG14: a quest's reward (rpg.talk) into the save: {"gold": n} and {"card": "rare_from_band"} (a rare or mythic from the
    region's tier band, any colour, drawn with the save's rng; the band's best tier first). The flag is already set by the talk.
    Returns the lines that say what was given."""
    from rpg.shop import candidates
    s, lines = game.save, []
    reward = reward if isinstance(reward, dict) else {}      # Round RPG16d: a hand-edited reward that isn't a table gives nothing
    if reward.get("gold"):
        try:                                     # Round RPG16d (review T3): a reward only gives - the save's gold never goes
            n = int(reward["gold"]) if not isinstance(reward["gold"], bool) else 0      # below 0 (a save below 0 won't load)
        except (TypeError, ValueError, OverflowError):
            n = 0
        if n > 0:
            s.gold += n
            lines.append(f"+{n} gold.")
        else:
            from rpg import notes
            notes.once(f"reward:gold:{reward['gold']!r}"[:200], f"a reward's gold {reward['gold']!r} is not a number above 0 - "
                                                               "nothing is given or taken")
    if reward.get("card") == "rare_from_band":
        lo, hi = game.world.band(s.world.get("region") or 1)
        rng = s.rng("quest_reward")
        for t in range(hi, lo - 1, -1):
            names = [n for n in candidates(t, "", game.pool, game.tiers(), game.lists(), game.numbers.shop_skip_prefixes)
                     if (game.pool.get(n).rarity or "C") in ("R", "M")]
            if names:
                pick = rng.choice(names)
                s.collection.add(pick, 1)
                lines.append(f"New card: {pick}.")
                break
    return lines


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
    encounter = game.save.pending_duel.get("encounter")
    rid = game.save.pending_duel.get("rare")
    title = game.save.pending_duel.get("name")                              # Round RPG18b: a foe with its own name
    gauntlet = bool(game.save.pending_duel.get("gauntlet"))                 # Round PROG5: the town gauntlet's duel
    duel_map = game.save.pending_duel.get("map") or game.save.world.get("map")
    entry = _rare_table().get(rid) if isinstance(rid, str) else None
    out = duel.finish(game.save, result, game.numbers, game.world, pool=game.pool, tier_table=game.tiers(), lists=game.lists(),
                      offer=True)                                           # Round PROG2: the reward cards are a choice
    out.update(enemy=enemy_name(kind), kind=kind, encounter=encounter)     # Round RPG14: the kind, for the boss's last words
    if isinstance(title, str) and title:
        out["enemy"] = title
    kind_xp = "rare" if entry else (encounter if encounter in ("dungeon", "boss") else "regular")
    from rpg import extras
    try:                                                                    # Round PROG5: the first win of the in-game day
        first = extras.first_win(game.save, out.get("result"))
    except (OSError, ValueError) as e:
        from rpg import notes
        notes.once("extras:read", f"progression.json's PROG5 tables couldn't be read: {e}")
        first = None
    if first:
        game.save.gold += first["gold"]
        out["gold"] = int(out.get("gold") or 0) + first["gold"]
        out["first_win"] = first
    try:                                                                    # Round PROG3: XP (and perhaps a level, a perk choice)
        from rpg import levels
        lv = levels.read()
        out["xp"] = levels.gain(game.save, levels.xp_for(out.get("result"), kind_xp, lv) + (first or {}).get("xp", 0), lv)
    except (OSError, ValueError) as e:
        from rpg import notes
        notes.once("levels:read", f"progression.json's levels couldn't be read, so no XP was given: {e}")
    if out.get("result") == "won":                                          # Round PROG1: the bestiary's page of this kind
        from rpg import progress
        progress.note_beaten(game.save, kind, [n for n, _src in out.get("cards") or ()])
        try:                                                                # Round PROG5: the bounty board's jobs count it
            from rpg import clock
            done = extras.bounty_on_win(game.save.world, kind, duel_map, clock.phase(clock.now(game.save.world)))
            if done:
                out["bounty_lines"] = [extras.BOUNTY_DONE_LINE.format(text=t) for t in extras.job_texts(done)]
        except (OSError, ValueError):
            pass
    if gauntlet:                                                            # Round PROG5: the gauntlet's count (and its prize)
        try:
            out["gauntlet"] = extras.after_gauntlet_duel(game, out.get("result")) or {}
        except (OSError, ValueError) as e:
            from rpg import notes
            notes.once("extras:gauntlet", f"the gauntlet couldn't be settled: {e}")
    if entry:                                                               # Round RPG16: a rare foe
        out.update(enemy=entry["name"], rare=rid)
        if out.get("result") == "won":
            from rpg import rare
            rare.on_beaten(game.save.world, rid)
            out["extra_lines"] = give_reward(game, entry.get("reward"))
    if out.get("result") == "won":                                          # Round PROG4: a boss's or a rare foe's gear (once)
        from rpg import gear
        found = gear.drop_for(game.save, "rares", rid) if entry else \
            (gear.drop_for(game.save, "bosses", kind) if encounter == "boss" else [])
        if found:
            out["extra_lines"] = list(out.get("extra_lines") or ()) + found
    try:
        from rpg import gear
        worn = gear.worn_line(game.save)
    except (OSError, ValueError):
        worn = ""
    if worn and out.get("result") in ("won", "lost", "conceded", "draw"):   # Round PROG4: what you wore, on the results screen
        out["worn"] = worn
    try:
        game.write()
    except OSError:
        back = rsave.SaveGame.from_dict(before, pool=game.pool)            # not written: as it was (the duel still marked),
        game.save.__dict__.update(back.__dict__)                            # in the same object the screens hold
        raise
    return out


def quest_xp(game):
    """Round PROG3: a quest handed in gives XP (progression.json xp.quest). Returns the lines to add to the reward's words (the save is
    written by the caller, as for the reward)."""
    from rpg import levels
    try:
        lv = levels.read()
        g = levels.gain(game.save, levels.xp_for("quest", None, lv), lv)
    except (OSError, ValueError):
        return []
    return [f"+{g['gained']} XP."] + levels.level_up_lines(g, lv) if g["gained"] else []


def choose_perk(game, perk_id):
    """Round PROG3: the perk picked on the perk screen goes into the save, which is written. ValueError when it isn't offered;
    OSError (undone in memory) when it can't be written. Returns the perk."""
    from rpg import levels
    before = game.save.to_dict()
    perk = levels.choose(game.save, perk_id)
    try:
        game.write()
    except OSError:
        back = rsave.SaveGame.from_dict(before, pool=game.pool)
        game.save.__dict__.update(back.__dict__)
        raise
    return perk


def choose_rewards(game, picks):
    """Round PROG2: the reward cards chosen on the results screen go into the collection and the save is written; returns the
    lines that say so. ValueError for a choice that doesn't fit; OSError (the choice undone in memory) when it can't be written."""
    from rpg import rewards
    before = game.save.to_dict()
    given = rewards.choose(game.save, list(picks))
    try:
        game.write()
    except OSError:
        back = rsave.SaveGame.from_dict(before, pool=game.pool)
        game.save.__dict__.update(back.__dict__)
        raise
    names = [n for n, _src in given]
    return [f"New card{'s' if len(names) != 1 else ''}: " + ", ".join(names) + "."] if names else []


def pending_pick_outcome(game):
    """Round PROG2: a reward still to choose (a game closed on the choosing screen) as an outcome the results screen opens with -
    the same cards again; None when there is none."""
    pp = game.save.pending_pick
    if not pp:
        return None
    return {"result": "won", "gold": 0, "cards": [], "offers": pp["groups"], "enemy": enemy_name(pp.get("kind")),
            "kind": pp.get("kind"), "again": True}


def outcome_lines(out, world_=None):
    """The lines that say what a settled duel did ([] for None)."""
    if not out:
        return []
    enemy = out.get("enemy") or "the enemy"
    worn = [out["worn"]] if out.get("worn") else []          # Round PROG4: "Worn: Copper Charm, Road Boots." (last line)
    r = out.get("result")
    if r == "won":
        if out.get("again"):                     # Round PROG2: the choice left open when the game closed
            lines = [f"Your reward from the {enemy} is still waiting."]
        else:
            first = out.get("first_win") or {}
            own = int(out.get("gold", 0) or 0) - int(first.get("gold", 0) or 0)
            lines = [f"You beat the {enemy}" + ("." if out.get("gauntlet") is not None and not own else f": +{own} gold.")]
            if out.get("won_back"):              # Round GOLD1: the gold it took from you, in that count
                lines.append(WON_BACK_LINE.format(n=out["won_back"], enemy=enemy))
            if first:                            # Round PROG5: the first win of the in-game day
                from rpg import extras
                lines.append(extras.FIRST_WIN_LINE.format(gold=first.get("gold", 0), xp=first.get("xp", 0)))
        sig = [n for n, src in out.get("cards") or [] if src == "signature"]
        cards = [n for n, src in out.get("cards") or [] if src != "signature"]
        if sig:                                  # Round PROG2: the card the boss guarded
            lines.append(f"He guarded {sig[0]}: it is yours.")
        if cards:
            lines.append(f"New card{'s' if len(cards) != 1 else ''}: " + ", ".join(cards) + ".")
        offers = out.get("offers") or []
        if offers:                               # Round PROG2: pick 1 of 3 for each reward card
            n, per = len(offers), max(len(g) for g in offers)
            lines.append(f"Choose your reward card: 1 of {per}." if n == 1 else
                         f"Choose your {n} reward cards: 1 of each {per}.")
        return lines + _extras_lines(out) + list(out.get("extra_lines") or ()) + worn
    if r in ("lost", "conceded") and isinstance(out.get("gauntlet"), dict):      # Round PROG5: a gauntlet loss costs nothing
        first = f"You conceded to the {enemy}." if r == "conceded" else f"The {enemy} beat you."
        return [first] + _extras_lines(out) + worn
    if r in ("lost", "conceded"):
        town = out.get("moved_to")
        if town:
            try:                                 # the town's name, never its id (the end screen passes no world: it is read here)
                town = (world_ if world_ is not None else world()).town(town).name
            except (KeyError, OSError, ValueError, TypeError, AttributeError):
                pass
        lost = -int(out.get("gold") or 0)
        first = f"You conceded to the {enemy}." if r == "conceded" else f"The {enemy} beat you."
        if lost and out.get("held_by"):          # Round GOLD1: the foe holds it - the place on a line of its own
            lines = [first, LOST_HELD_LINE.format(n=lost, enemy=enemy)]
            if out.get("inn"):
                lines.append("You woke at the inn.")                 # the innkeeper's words follow in her box (rpg_screens)
            elif town:
                lines.append(f"You are back in {town}.")
            return lines + worn
        if out.get("inn"):                       # Round RPG14: you wake at the inn you last rested at (rpg.talk); the
            return [first, (f"You lost {lost} gold" if lost else "You had no gold to lose") +      # innkeeper's words follow
                    " and woke at the inn."] + worn                                            # in her box (rpg_screens)
        second = (f"You lost {lost} gold" if lost else "You had no gold to lose") + (f" and are back in {town}." if town else ".")
        return [first, second] + worn
    if r == "draw":
        return [f"A draw with the {enemy}: nothing won or lost."] + _extras_lines(out) + worn
    return []


def _extras_lines(out):
    """Round PROG5: the bounty jobs a duel finished and the gauntlet's lines (its count, and its prize once it is over)."""
    return list(out.get("bounty_lines") or ()) + list((out.get("gauntlet") or {}).get("lines") or ())


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
