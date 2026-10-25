# SPDX-License-Identifier: GPL-3.0-or-later
"""Saying a data problem once in crash_log.txt (Round RPG16d; the rule RPG16b's rpg.explore._note_once started). No pygame.

The Adventure's data files (events.json, rare_spawns.json, the maps' schedules) are Karl's to edit by hand, and a save may hold a
value an older copy of the game wrote. A value the game can't use is refused or clamped where it is read, and said here - once a
run for each `what`, so a value read on every frame doesn't fill the log. crashlog is the program's own module; in a bare tool
run (no crash log) nothing is said.

    once(what, text)    say "Adventure: <text>" the first time `what` is seen this run; True when it was said
"""
NOTED = set()


def once(what, text):
    if what in NOTED:
        return False
    NOTED.add(what)
    try:
        import crashlog
        crashlog.note("Adventure: " + text)
    except Exception:                                    # no crash log in a bare tool run: say nothing
        pass
    return True
