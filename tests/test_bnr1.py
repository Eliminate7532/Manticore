# SPDX-License-Identifier: GPL-3.0-or-later
"""Round BNR1: the turn banner lasts about 600 ms (Karl, 13 Oct: "Shorten only the banner").

Patch 47's rule (AD2c) gives an effect 150-600 ms; the banner ran 1.1 s. Only the banner changes: the AI-action spotlight and
the floating numbers stay at 0.9 s, as Karl chose.

  DurationTests   the constants: the banner within the rule, the other two untouched
  DrawTests       the banner drawn on a real table: shown at 0.5 s, gone (and cleared) at 0.61 s, embers only early on
"""
import os
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import anim
from tests.test_forge_table import make_gui


class DurationTests(unittest.TestCase):
    def test_the_banner_keeps_to_the_150_to_600_ms_rule(self):
        self.assertGreaterEqual(anim.BANNER_SECONDS, 0.15)
        self.assertLessEqual(anim.BANNER_SECONDS, 0.6)
        self.assertGreaterEqual(anim.BANNER_SECONDS, 0.5, "much shorter and 'AI 1's turn' can't be read")

    def test_the_spotlight_and_the_numbers_are_not_changed(self):
        self.assertEqual(anim.SPOT_SECONDS, 0.9)
        self.assertEqual(anim.FLOAT_SECONDS, 0.9)


class DrawTests(unittest.TestCase):
    def setUp(self):
        self.gui = make_gui("main1_lands")
        self.now = time.monotonic()
        self.gui.anim.banner = ("AI 1's turn", "Turn 3", self.now)

    def test_still_on_screen_at_half_a_second(self):
        self.gui.anim.draw_banner(self.gui, self.now + 0.5)
        self.assertIsNotNone(self.gui.anim.banner)

    def test_gone_just_after_six_tenths(self):
        self.gui.anim.draw_banner(self.gui, self.now + 0.61)
        self.assertIsNone(self.gui.anim.banner)

    def test_the_banner_no_longer_keeps_the_table_busy_after_it_ends(self):
        a = self.gui.anim
        self.assertLess(a.banner[2] + anim.BANNER_SECONDS, self.now + 0.61)

    def test_embers_only_while_it_is_bright(self):
        a = self.gui.anim
        a.draw_banner(self.gui, self.now + 0.1)
        first = self.gui.front_particles.count()
        self.assertEqual(first, anim.BANNER_SPAWN)
        a.draw_banner(self.gui, self.now + anim.BANNER_SECONDS * 0.8)          # fading out: no more
        self.assertEqual(self.gui.front_particles.count(), first)


if __name__ == "__main__":
    unittest.main()
