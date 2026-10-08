Round UI6: the recorded layouts that tests/test_ui6.py compares today's table with (Log: Always + Focus: Always must be exactly
the layout of patch 48, rectangle by rectangle).

layout_baseline.json        Linux.   Recorded from patch 48's tree (commit acee719) on Linux: Python 3.10.12, pygame-ce 2.5.8,
                                      SDL 2.32.10, SDL_ttf 2.24.0.
layout_baseline_win32.json  Windows. Recorded from a byte-identical copy of the same commit (all 469 files checked against the
                                      commit's blobs) on Karl's PC, Windows 11, Python 3.14.7, pygame-ce 2.5.8, SDL 2.32.10,
                                      SDL_ttf 2.24.0, with tools\ui6_layout_diff.py (2026-10-08).

Why two: in a 4-player game at 200% text an opponent's board starts after its command-zone frame, and that frame is as wide as the
word "CMD" in the tiny font (forge_table.py, cmd_frame_for). Windows measures that word 1 px differently from Linux, so the three
cards of each opponent sit 1 px left or right: 6 of the 28 tables, 9 cards each, nothing else (a test pins exactly that). The program
is the same on both; only the ruler differs. A 1 px tolerance in the comparison would hide real 1 px mistakes, so there are two exact files.

To record one for another kind of computer: copy a clean checkout of patch 48's program, put tests/ui6_layout.py in its tests
folder, add the computer's sys.platform to BASELINE_FILES, and run  python -m tests.ui6_layout --write  there.
