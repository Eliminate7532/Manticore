# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SND1 (Karl, 3 Nov 2026 07:49 CST: "Add a wolf "howl" sound to the game that plays at dusk." and "Add a rooster sound at
dawn."): when the Adventure's clock reaches dusk or dawn, the world says so. No pygame.

The one rule (rpg.clock.crossed finds the boundaries; this keeps track of what was heard):

  * The clock reaching 18:00 howls (rpg.howl), reaching 05:00 crows (rpg.rooster) - by walking, waiting, an event's minutes or a
    rest that ends past or on that time (Rest until dusk howls when you wake; a night's sleep that ends after 05:00 crows when you
    wake). Once per crossing; a jump of more than a day sounds each boundary once (rpg.clock.crossed).
  * Never when a save is loaded or a new game starts: the first look at a game only notes the time (reset()).
  * Only where it can be heard: on the map (not a shop's window, the deck editor or the Collection), outside a duel's way in
    ("fight") and the results screen ("result"), on a map that follows the clock (rpg.explore: outdoor = true - the Ashfen
    Outskirts and Lantern Hollow; not the Warrens 1 and 2). A crossing made where it can't be heard waits: it sounds when you are
    back, if you are still within that phase (the dusk that began at that 18:00, the dawn that began at that 05:00), else never.
  * A crossing made where it can be heard sounds at once, whatever the phase now is (a night's sleep wakes you at 07:00, by day:
    the rooster crowed at 05:00 while you slept, and you hear it as you wake).

The tracker lives on the table (ForgeTable), not in the Adventure's window, because a duel closes that window and the table opens a
new one on the same game after it: tick(key=...) keeps one game's state across that and starts again for any other game.
"""
from rpg import clock

CUES = {"dusk": "rpg.howl", "dawn": "rpg.rooster"}      # the boundary -> the cue it plays
HEARD_STAGES = ("map",)                                # rpg_screens.AdventureFlow.stage values where the world is heard
SILENT_SUBS = ("fight", "result")                      # ... and its .sub values where it isn't (the duel's way in, the results)


class Tracker:
    def __init__(self):
        self.key = None             # the game it follows: (folder, slot, the save's name)
        self.seen = None            # the clock's time at the last look
        self.waiting = {}           # boundary name -> its minute: crossed where it couldn't be heard

    def reset(self, key, minutes):
        """A save was loaded or a new game began: note the time, sound nothing."""
        self.key, self.seen, self.waiting = key, float(minutes), {}

    def tick(self, key, minutes, can_hear):
        """One look: returns the cues to play now, in time order."""
        minutes = float(minutes)
        if key != self.key or self.seen is None or minutes < self.seen:   # another game, or a clock that went back: a fresh start
            self.reset(key, minutes)
            return []
        fresh = clock.crossed(self.seen, minutes, tuple(CUES))
        self.seen = minutes
        if not can_hear:
            for name, at in fresh:
                self.waiting[name] = at                        # a later crossing of the same boundary replaces it
            return []
        out = [(at, CUES[name]) for name, at in fresh]
        for name, at in self.waiting.items():
            if clock.phase(minutes) == name and clock.phase_key(minutes) == at and all(c != CUES[name] for _a, c in out):
                out.append((at, CUES[name]))                   # still that phase: heard now; otherwise it's gone
        self.waiting = {}
        return [c for _at, c in sorted(out)]


def can_hear(flow):
    """Whether the Adventure's window `flow` (rpg_screens.AdventureFlow) is where the world can be heard now."""
    if getattr(flow, "stage", None) not in HEARD_STAGES or getattr(flow, "sub", None) in SILENT_SUBS:
        return False
    ex = getattr(flow, "explorer", None)
    area = getattr(ex, "area", None)
    return bool(getattr(area, "outdoor", False))
