# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AD2c: the readability animations (anim.py)."""
import copy
import os
import sys
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anim
import events
import forge_dialogs as dlg
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), time.monotonic())


def opp_card(gui, zone="battlefield"):
    opp = gui.session.opponents()[0]
    return opp["zones"][zone][0]


def my_card(gui):
    return gui.session.me()["zones"]["battlefield"][0]


class SpotlightTests(unittest.TestCase):
    def test_an_ai_spell_is_shown_and_mine_is_not(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.feed(gui, [beat("cast", card=my_card(gui)["id"])], now)
        self.assertEqual(len(gui.anim.spots), 0)
        c = opp_card(gui)
        gui.anim.feed(gui, [beat("cast", card=c["id"])], now)
        gui.anim.watch(gui, now)
        self.assertIsNotNone(gui.anim.spot)
        self.assertEqual((gui.anim.spot.name, gui.anim.spot.verb, gui.anim.spot.who), (c["name"], "casts", "AI 1"))
        for k, verb in (({"trigger": True}, "trigger"), ({"ability": True}, "activates")):
            a = anim.Animator()
            a.feed(gui, [beat("cast", card=c["id"], **k)], now)
            self.assertEqual(a.spots[0].verb, verb)

    def test_one_at_a_time_and_a_long_burst_becomes_plus_n_more(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        c = opp_card(gui)
        gui.anim.feed(gui, [beat("cast", card=c["id"]) for _ in range(9)], now)
        self.assertEqual(len(gui.anim.spots), anim.SPOT_MAX_QUEUE)
        self.assertEqual(gui.anim.spots[-1].more, 9 - anim.SPOT_MAX_QUEUE)
        gui.anim.watch(gui, now)
        first = gui.anim.spot
        gui.anim.watch(gui, now + anim.SPOT_SECONDS / 2)
        self.assertIs(gui.anim.spot, first)                       # still the first one
        gui.anim.watch(gui, now + anim.SPOT_SECONDS + 0.01)
        self.assertIsNot(gui.anim.spot, first)                    # then the next

    def test_a_question_for_me_cuts_the_queue_at_once(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        c = opp_card(gui)
        gui.anim.feed(gui, [beat("cast", card=c["id"]) for _ in range(3)], now)
        gui.anim.watch(gui, now)
        gui.modal = dlg.MessageDialog("Choose", "something")        # Forge asks me something
        self.assertTrue(gui.asks_me())
        gui.anim.watch(gui, now + 0.01)
        self.assertEqual(len(gui.anim.spots), 0)
        self.assertLessEqual(gui.anim.spot.end, now + 0.01 + anim.SPOT_FAST + 1e-6)

    def test_a_plain_priority_pass_does_not_cut_it(self):
        gui = make_gui("main1_lands")
        self.assertTrue(gui.prompt().get("message", "").startswith("Priority"))
        self.assertFalse(gui.asks_me())

    def test_it_never_lags_more_than_max_lag(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        c = opp_card(gui)
        gui.anim.feed(gui, [beat("cast", card=c["id"]), beat("cast", card=c["id"])], now)
        gui.anim.watch(gui, now)
        gui.anim.watch(gui, now + anim.MAX_LAG + 0.1)
        self.assertEqual(len(gui.anim.spots), 0)


class NumbersTests(unittest.TestCase):
    def test_damage_and_counters_float_off_the_card(self):
        gui = make_gui("main1_lands")
        cid = opp_card(gui)["id"]
        now = time.monotonic()
        gui.anim.feed(gui, [beat("damage_card", card=cid, amount=3), beat("counters", card=cid, counter="P1P1", old=0, new=2)], now)
        self.assertEqual([f[0] for f in gui.anim.floats], ["-3", "+2 +1/+1"])
        gui.anim.draw_floats(gui, now + 0.3)
        self.assertEqual(len(gui.anim.floats), 2)
        gui.anim.draw_floats(gui, now + anim.FLOAT_SECONDS + 0.01)
        self.assertEqual(gui.anim.floats, [])

    def test_life_counts_from_old_to_new(self):
        a = anim.Animator()
        now = 100.0
        self.assertEqual(a.life(1, 40, now), 40)
        self.assertEqual(a.life(1, 30, now + 1), 40)                # the change starts here
        mid = a.life(1, 30, now + 1 + anim.LIFE_SECONDS / 3)
        self.assertTrue(30 < mid < 40, mid)
        self.assertEqual(a.life(1, 30, now + 1 + anim.LIFE_SECONDS + 0.01), 30)

    def test_the_panel_prints_the_counting_value(self):
        gui = make_gui("main1_start")
        st = copy.deepcopy(gui.state)
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        me["life"] -= 10
        gui.session.handle(st)
        frame(gui)
        shown = gui.anim.lives[me["id"]]
        self.assertEqual(shown[1], me["life"])


class MotionTests(unittest.TestCase):
    def test_tapping_turns_the_card_then_rests(self):
        a = anim.Animator()
        now = 50.0
        a.feed(make_gui("main1_lands"), [beat("tap", card=7, tapped=True)], now)
        mid = a.tap_angle(7, True, now + anim.TAP_SECONDS / 2)
        self.assertTrue(0 < mid < 90, mid)
        self.assertIsNone(a.tap_angle(7, True, now + anim.TAP_SECONDS + 0.01))

    def test_an_attacker_steps_forward_and_back(self):
        a = anim.Animator()
        self.assertEqual(a.step(5, False, 0.0), 0.0)
        a.step(5, True, 0.0)
        self.assertAlmostEqual(a.step(5, True, anim.STEP_SECONDS + 0.01), anim.STEP)
        a.step(5, False, 1.0)
        self.assertAlmostEqual(a.step(5, False, 1.0 + anim.STEP_SECONDS + 0.01), 0.0)

    def test_my_attacker_is_drawn_higher_with_animations_on(self):
        st = copy.deepcopy(load_state("main1_lands"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        creature = next(c for c in me["zones"]["battlefield"] if not c.get("isLand"))
        creature["attacking"] = True
        cid = creature["id"]
        on, off = make_gui(copy.deepcopy(st)), make_gui(copy.deepcopy(st))
        off.animations = False
        for k in range(3):
            frame(on)
            frame(off)
            time.sleep(anim.STEP_SECONDS / 2)
        frame(on)
        frame(off)
        self.assertLess(on.card_rects[cid].y, off.card_rects[cid].y)

    def test_combat_lines_grow(self):
        a = anim.Animator()
        a.lines[(1, ("b", 2))] = 10.0
        self.assertEqual(a.grow(1, ("b", 2), 10.0), 0.0)
        self.assertEqual(a.grow(1, ("b", 2), 10.0 + anim.GROW_SECONDS), 1.0)
        self.assertEqual(a.grow(9, ("d", "p1"), 10.0), 1.0)          # a line seen for the first time this frame is drawn whole until watch() notes it


class BannerTests(unittest.TestCase):
    def test_a_new_turn_gets_a_banner_but_the_first_snapshot_does_not(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.watch(gui, now)
        self.assertIsNone(gui.anim.banner)
        st = copy.deepcopy(gui.state)
        opp = gui.session.opponents()[0]
        st["turn"] += 1
        st["activePlayer"] = opp["id"]
        gui.session.handle(st)
        gui.anim.watch(gui, now + 0.1)
        self.assertEqual(gui.anim.banner[0], "AI 1's turn")
        st2 = copy.deepcopy(st)
        st2["turn"] += 1
        st2["activePlayer"] = st["me"]
        gui.session.handle(st2)
        gui.anim.watch(gui, now + 0.2)
        self.assertEqual(gui.anim.banner[0], "Your turn")


class SwitchAndSafetyTests(unittest.TestCase):
    def test_animations_off_means_none_of_it(self):
        gui = make_gui("main1_lands")
        gui.animations = False
        gui.beats_this_frame = [beat("cast", card=opp_card(gui)["id"]), beat("damage_card", card=opp_card(gui)["id"], amount=2)]
        gui.apply_effects(time.monotonic())
        self.assertEqual((len(gui.anim.spots), gui.anim.spot, gui.anim.floats), (0, None, []))

    def test_animations_never_send_anything_to_forge(self):
        sent = []
        for on in (True, False):
            gui = make_gui("main1_lands")
            gui.animations = on
            c = opp_card(gui)["id"]
            gui.beats_this_frame = [beat("cast", card=c), beat("tap", card=c, tapped=True), beat("damage_card", card=c, amount=1)]
            gui.apply_effects(time.monotonic())
            frame(gui, 3)
            sent.append(list(gui.session.sent))
        self.assertEqual(sent[0], sent[1])

    def test_everything_draws_at_every_size(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = make_gui("main1_lands", size, scale)
                now = time.monotonic()
                c = opp_card(gui)["id"]
                gui.anim.feed(gui, [beat("cast", card=c), beat("damage_card", card=c, amount=4)], now)
                gui.anim.watch(gui, now)
                gui.anim.banner = ("AI 1's turn", "Turn 3", now)
                frame(gui)
                self.assertTrue(gui.activity(time.monotonic())[0])


if __name__ == "__main__":
    unittest.main()
