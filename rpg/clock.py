# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's clock (Round RPG15, Karl 23 Oct 2026 18:56: "A" - "In-game time that passes while you walk. A full day is about
24 minutes of walking, and sleeping at the inn skips to morning"). No pygame.

    now(world)                    the time, in game minutes since the game began (save.world["clock"]; a game from before RPG15
                                  starts at START)
    advance(world, seconds)       walking time: GAME_MINUTES_PER_SECOND game minutes for every second the walker moves
    phase(minutes)                "dawn" / "day" / "dusk" / "night"
    clock_text(minutes)           "18:20"
    to_morning(world)             sleeping: on to the next MORNING (07:00)
    to_dusk(world)                Round RPG17: resting at the inn by day - on to the next DUSK (18:00)
    stamp(minutes)                Round RPG17 (Karl 24 Oct Q10): the corner window's line, "Day 2, Night 21:00"
    level(minutes, dusk, night)   how dark an outdoor map is at that time - 0 by day, `dusk` at dusk, `night` at night, eased
                                  between them over each change (KEYS)

Only walking moves the clock: standing still, a window, a talk, the encounter screen and a duel don't (my reading of "passes while
you walk"). A dungeon's map has no clock of its own (rpg.explore: only a map with the property outdoor = true follows it), but walking
there moves the clock all the same, so you come up at a later hour.

My estimates (to tune while Karl plays): the phases' hours, START, the dawn level (DAWN_SHARE of the dusk's) and how long each change
takes.
"""
import math

DAY = 24 * 60                       # game minutes in a day
GAME_MINUTES_PER_SECOND = DAY / (24 * 60.0)   # a day in 24 minutes of walking: one game minute a second (Karl's "about 24 minutes")
START = 7 * 60                      # a new game begins at 07:00 (Round RPG17, Karl 24 Oct Q2: "Start at 07:00"; was 17:00)
MORNING = 7 * 60                    # the inn wakes you at 07:00
DUSK = 18 * 60                      # Round RPG17 (Karl 24 Oct Q5): "Rest until dusk" at the inn wakes you at 18:00
PHASES = (("night", 0), ("dawn", 5 * 60), ("day", 7 * 60), ("dusk", 18 * 60), ("night", 20 * 60))   # (name, starts at) - my estimate
DAWN_SHARE = 0.5                    # dawn is half as dark as dusk - my estimate


def now(world):
    v = world.get("clock")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 else float(START)   # RPG16b: R9


def advance(world, seconds):
    """Walking time: the clock moves on by `seconds` of walking. Returns the new time."""
    t = now(world) + max(0.0, float(seconds)) * GAME_MINUTES_PER_SECOND
    world["clock"] = t                  # Round RPG15c (review R2): not rounded - rounding each tick ran the clock 2% fast at 60 ticks
    return t


def minute_of_day(minutes):
    return float(minutes) % DAY


def phase(minutes):
    m = minute_of_day(minutes)
    name = PHASES[0][0]
    for n, start in PHASES:
        if m >= start:
            name = n
    return name


def clock_text(minutes):
    m = int(minute_of_day(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def day_number(minutes):
    """1 on the first day."""
    return int(float(minutes) // DAY) + 1


def _on_to(world, minute):
    t = now(world)
    base = t - minute_of_day(t)
    target = base + minute
    if target <= t:
        target += DAY
    world["clock"] = round(target, 3)
    return world["clock"]


def to_morning(world):
    """Sleeping at the inn: on to the next 07:00 - the same day's when it is still before 07:00. Returns the new time.
    (Round RPG17, Karl 24 Oct: a lost duel no longer calls this - waking at the inn after a loss keeps the clock.)"""
    return _on_to(world, MORNING)


def to_dusk(world):
    """Round RPG17 (Karl 24 Oct Q5, "Add 'Rest until dusk' (18:00)"): resting at the inn by day - on to the next 18:00, the same
    day's when it is still before 18:00. Returns the new time."""
    return _on_to(world, DUSK)


def stamp(minutes):
    """Round RPG17 (Karl 24 Oct Q10, "Add the day"): "Day 2, Night 21:00"."""
    return f"Day {day_number(minutes)}, {phase(minutes).capitalize()} {clock_text(minutes)}"


PHASE_NAMES = ("dawn", "day", "dusk", "night")    # Round RPG16: the words "phase:<p>" knows (rpg.conditions)


def phase_key(minutes):
    """Round RPG16: the game minute at which the current phase began - one number for a whole phase, so a night from 20:00 on day 1
    has one key at 23:00 day 1 and at 03:00 day 2 (a rare spawn rolls once per key)."""
    t = float(minutes)
    base = t - minute_of_day(t)
    m = minute_of_day(t)
    start = PHASES[0][1]
    for _n, s in PHASES:
        if m >= s:
            start = s
    key = base + start
    if start == PHASES[0][1] and PHASES[0][0] == PHASES[-1][0]:      # the phase the day starts in began the evening before
        key = base - DAY + PHASES[-1][1]
    return key


def pass_minutes(world, n):
    """Round RPG16: the clock moves on n game minutes (an event's "minutes"); never back. Returns the new time."""
    try:
        n = float(n)
    except (TypeError, ValueError, OverflowError):        # RPG16d (review T9): an int too big for a float raises OverflowError
        n = 0.0
    if not (n > 0 and math.isfinite(n)):                  # also refuses NaN; RPG16c (review S4): and infinity
        return now(world)
    t = round(now(world) + n, 3)
    if not math.isfinite(t):                              # RPG16d (review T9): two finite numbers can sum to infinity, which now()
        return now(world)                                 # reads as 17:00 on day 1 - the clock would go back
    world["clock"] = t
    return world["clock"]


def keys(dusk, night):
    """(minute of the day, darkness) points the darkness eases between: night until 05:00, light by 07:30, dark again from 17:30
    (dusk by 18:30, night by 20:00)."""
    dawn = dusk * DAWN_SHARE
    return ((0, night), (5 * 60, night), (6 * 60, dawn), (7 * 60 + 30, 0.0), (17 * 60 + 30, 0.0), (18 * 60 + 30, dusk),
            (20 * 60, night), (DAY, night))


def level(minutes, dusk, night):
    m = minute_of_day(minutes)
    pts = keys(dusk, night)
    for (m0, v0), (m1, v1) in zip(pts, pts[1:]):
        if m0 <= m <= m1:
            if m1 == m0:
                return v1
            k = (m - m0) / (m1 - m0)
            k = k * k * (3 - 2 * k)                       # eased, so a change starts and ends gently
            return round(v0 + (v1 - v0) * k, 4)
    return night
