# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG12: the RPG5/RPG6 placeholder maps (sample_region, sample_dungeon) are test fixtures now, in tests/fixtures/rpg_maps/.
The game's own maps (rpg/maps/, built by tools/rpg_make_maps.py) changed under the tests that pin the placeholders' geometry, so
those test modules point rpg.explore at the fixtures while they run:

    from tests.rpg_fixture_maps import setUpModule, tearDownModule      # noqa: F401  (unittest calls them)
"""
import os

from rpg import explore

FIXTURE_MAPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "rpg_maps")
_saved = []


def setUpModule():
    _saved.append(explore.MAPS_DIR)
    explore.MAPS_DIR = FIXTURE_MAPS
    explore.forget_areas()


def tearDownModule():
    explore.MAPS_DIR = _saved.pop() if _saved else explore.MAPS_DIR
    explore.forget_areas()
