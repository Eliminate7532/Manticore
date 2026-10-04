# SPDX-License-Identifier: GPL-3.0-or-later
"""
Makes tests/ a package, and - Round 27a - keeps the whole suite out of the real project's own files.

Before this, a test run could write into the project's real crash_log.txt, forge_engine.log, perf_log.txt
or saves/ (the Round 22 banner tests once wrote "Forge not answering" into the real crash log). Every
module that touches those paths reads MANTICORE_DATA_DIR (crashlog.py, forge_client.py, forge_table.py);
setting it here, before any test module runs, points all of them at one fresh temp folder for the whole
run. tools/quick_tests.py additionally checks that the real files' size and mtime never move, in case a
test imports one of those modules before this file gets a chance to run.
"""
import atexit
import os
import shutil
import tempfile

_DATA_DIR = tempfile.mkdtemp(prefix="manticore_test_data_")
os.environ["MANTICORE_DATA_DIR"] = _DATA_DIR
atexit.register(shutil.rmtree, _DATA_DIR, True)
# Round UX1: the first-game tour never starts by itself in a test (tests/test_ux1.py clears this where it tests the tour)
os.environ["MANTICORE_NO_TOUR"] = "1"
# Round BAN1: no banned-list refresh or card-data fetch from the network in a test (tests/test_ban1.py clears it where needed)
os.environ["MANTICORE_OFFLINE"] = "1"
# Round 30: no test ever looks for an update on the network (tests/test_round30.py sets MANTICORE_UPDATE_TEST where it tests that)
os.environ["MANTICORE_NO_UPDATE_CHECK"] = "1"
# Patch 38: Forge's card-name index is never saved into the project's own cache/ by a test (tests/test_patch38.py saves to a temp file)
os.environ["MANTICORE_NO_CARD_INDEX_SAVE"] = "1"
