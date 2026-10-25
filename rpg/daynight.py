# SPDX-License-Identifier: GPL-3.0-or-later
"""Who and what is on a map at each hour (Round RPG16, claude/SPEC_RPG16_DAYNIGHT_2026-10-23.md sections 2.3-2.4). No pygame.

A map object (a person, a foe, a rare) may carry `when` - rpg.conditions' words, "a,b" on the map, all must hold. rpg.explore asks
this module which of them are out now, and this module keeps the rule that things come and go out of sight:

    Presence.update(world, minutes, view, walker_tile, force=False)
        when the clock or the flags change what should be there, a thing starts or stops being there only once its tile is out of
        view (the camera's view plus a margin, `view` = (col0, row0, col1, row1) inclusive), and never on the walker's tile
        (`walker_tile` is one tile or, Round RPG16c, a set of the tiles the walker's feet are over);
        force=True (entering a map, loading, waking at the inn) sets everything at once - but still never on the walker's tile
        (Round RPG16b, review R4: a load put Wenna on the walker and walled it in). `tiles` = {key: tile} where a thing stands now
        (a foe that wandered off its spot - review R2); an item without one is judged by its own tile.
        Round RPG16b (review R3): a person's places are one group (Item.group, the person's id): the person moves from one place
        to the other only when every place that changes is out of view, so they are never in two places at once, nor in none.
        Round RPG17 (Karl 24 Oct, S1 (b)): or, with walk=, the person walks it in sight (rpg.explore.Mover) - in Lantern Hollow the
        square is on screen from every tile, so the out-of-sight rule alone never moved Hesk, Pell or Wenna. While a person walks
        they are at none of their places (not talked to); a place under the walker still waits for it to go (review S5).

The checks a map's people need (Area.check and the round's tests):
    clashes(whens)            for one person with several places: every phase and true/false mix of the flags and quest states
                              their `when`s name, where two places hold at once ([] = none)
    gaps(whens)               ... where none holds (a person who must always be somewhere: the innkeeper, the shopkeepers, the
                              hall keeper, the quest givers)
"""
import itertools

from rpg import clock, conditions, notes

ALWAYS_SOMEWHERE = ("innkeeper", "card_seller", "pack_seller", "hall_keeper", "elder", "lamplighter")


class Item:
    """One thing on the map that may come and go: key (its saved spot or the person's id + marker), when (a list), tile; test
    (world, minutes) -> bool instead of the `when`, for a rare (rpg.rare.is_out: its when, its cooldown and its roll); group (Round
    RPG16b): the person's id, so a person's places change together (None: the item is a group of its own)."""
    __slots__ = ("key", "when", "tile", "test", "group")

    def __init__(self, key, when, tile, test=None, group=None):
        self.key, self.when, self.tile, self.test, self.group = key, list(when or ()), tuple(tile), test, group
        if test is not None and not self.when:
            self.when = ["?"]                          # it comes and goes (wanted() asks the test)


def wanted(item, world, minutes):
    """Whether the item should be out. Round RPG16d (review T1): a test or a `when` that can't be read is said once in
    crash_log.txt and the item stays away - never an exception out of every refresh (the walk would stop on every frame)."""
    try:
        if item.test is not None:
            return bool(item.test(world, minutes))
        return conditions.all_hold(item.when, world, minutes=minutes)
    except conditions.ConditionError as e:
        notes.once(f"when:{item.key}", f"{item.key}'s when can't be read ({e}) - it stays away")
        return False
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, OverflowError) as e:
        notes.once(f"when:{item.key}", f"{item.key} can't be judged ({type(e).__name__}: {e}) - it stays away")
        return False


def in_view(tile, view):
    return view is not None and view[0] <= tile[0] <= view[2] and view[1] <= tile[1] <= view[3]


class Presence:
    """Which of a map's items are there now. Items with no `when` are always there and never change."""

    def __init__(self, items):
        self.items = list(items)
        self.present = {it.key for it in self.items if not it.when}
        self.started = False
        self.transit = {}                  # Round RPG17: {group: [keys]} - a person walking to these places (not there yet)

    def update(self, world, minutes, view=None, walker_tile=None, force=False, tiles=None, walk=None):
        """Bring the items up to date; returns the keys that came or went.
        Round RPG17 (Karl 24 Oct, S1 "(b) people walk off and on, animated"): walk(group, leave, arrive) -> bool is asked for a
        person's change that would happen in view; True means the person walks it (rpg.explore.Mover): the places they leave
        are left now, the ones they walk to are in self.transit until arrived(group). force=True drops every walk."""
        changed = []
        force = force or not self.started
        self.started = True
        if force:
            self.transit = {}
        if walker_tile is None:
            walker = set()
        elif walker_tile and isinstance(next(iter(walker_tile)), (tuple, list)):
            walker = {tuple(t) for t in walker_tile}    # RPG16c (review S2): every tile the walker's feet are over
        else:
            walker = {tuple(walker_tile)}
        tiles = tiles or {}

        def tile_of(it):
            t = tiles.get(it.key)
            return tuple(t) if t is not None else it.tile

        groups = {}
        for it in self.items:
            if it.when:
                groups.setdefault(it.key if it.group is None else ("group", it.group), []).append(it)
        for gkey, members in groups.items():
            if gkey in self.transit:
                continue                                  # RPG17: walking there - arrived() puts them in place
            want = {it.key for it in members if wanted(it, world, minutes)}
            have = {it.key for it in members if it.key in self.present}
            if want == have:
                continue
            arrive = [it for it in members if it.key in want and it.key not in have]
            leave = [it for it in members if it.key in have and it.key not in want]
            on_walker = [it for it in arrive if tile_of(it) in walker]
            if force and on_walker and members[0].group is not None:
                # RPG17 (review S5): a person whose new place is under the walker stays at the old one - and after a load (nobody
                # placed yet) stands at another of their places, then walks over once the walker steps off
                if not have:
                    other = next((it for it in members if it.key not in want and tile_of(it) not in walker), None)
                    if other is not None:
                        self.present.add(other.key)
                        changed.append(other.key)
                continue
            if force:
                arrive = [it for it in arrive if it not in on_walker]          # never on the walker, even at once (R4)
            elif on_walker:
                continue                                  # never onto the walker: they stay where they are until it goes (S5)
            elif any(in_view(tile_of(it), view) for it in arrive + leave):
                if walk is not None and members[0].group is not None and walk(gkey, leave, arrive):
                    for it in leave:                      # RPG17: they walk it, in sight
                        self.present.discard(it.key)
                        changed.append(it.key)
                    self.transit[gkey] = [it.key for it in arrive]
                continue                                  # it (or every place of a person) changes out of sight, together (R3)
            for it in leave:
                self.present.discard(it.key)
                changed.append(it.key)
            for it in arrive:
                self.present.add(it.key)
                changed.append(it.key)
        return changed

    def arrived(self, gkey):
        """Round RPG17: a walking person reached their place (or walked off the map: no keys). Returns the keys now there."""
        keys = self.transit.pop(gkey, None) or []
        for k in keys:
            self.present.add(k)
        return keys

    def is_present(self, key):
        return key in self.present


def _names(whens):
    """The flags and quests the `when`s name: ([flag, ...], [quest id, ...])."""
    flags, quests = set(), set()
    for when in whens:
        for c in when:
            body = c.lstrip("!")
            if body.startswith("flag:"):
                flags.add(body[5:])
            elif body.startswith("quest:"):
                quests.add(body[6:].partition("=")[0])
    return sorted(flags), sorted(quests)


def _worlds(whens):
    """Every world the `when`s can tell apart: each phase (a time in it) x each true/false mix of their flags x each state of their
    quests. Yields (description, world, minutes)."""
    flags, quests = _names(whens)
    phase_times = {}
    for name, start in clock.PHASES:
        phase_times.setdefault(name, start + 1)
    for phase, t in phase_times.items():
        for fl in itertools.product((False, True), repeat=len(flags)):
            for st in itertools.product(conditions.STATES, repeat=len(quests)):
                w = {"flags": [f for f, on in zip(flags, fl) if on],
                     "quests": {q: {"state": s} for q, s in zip(quests, st)}}
                desc = ", ".join([phase] + [("" if on else "!") + f for f, on in zip(flags, fl)]
                                 + [f"{q}={s}" for q, s in zip(quests, st)])
                yield desc, w, t


def clashes(whens):
    """Where two of one person's places hold at once."""
    whens = [list(w or ()) for w in whens]
    out = []
    for desc, w, t in _worlds(whens):
        n = sum(conditions.all_hold(when, w, minutes=t) for when in whens)
        if n > 1:
            out.append(desc)
    return out


def gaps(whens):
    """Where none of a person's places holds (they would be nowhere)."""
    whens = [list(w or ()) for w in whens]
    return [desc for desc, w, t in _worlds(whens) if not any(conditions.all_hold(when, w, minutes=t) for when in whens)]
