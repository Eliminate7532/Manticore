# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG16: day and night - the condition words, the clock's phase keys, rare spawns and random events
(claude/SPEC_RPG16_DAYNIGHT_2026-10-23.md; Karl 23 Oct 2026 18:55: "Town and overworld should have day night cycles these day night
cycles should have varying NPC's and enemies maybe even rare spawns and random events", 18:56: "A").

  ConditionTests   rpg.conditions: phase words (spec test 1), their problems, map words, "!quest"
  ClockTests       clock.phase_key and pass_minutes (spec test 2)
  RareTests        rpg/data/rare_spawns.json and rpg.rare: the check, the chance (12), staying put (13), the cooldown (14)
  EventTests       rpg/data/events.json and rpg.events: the check, the gap (16), the rules over 50 game days (17), effects (19)
  RareDuelTests    a rare foe's fight question, encounter screen, win and loss (15)
  PresenceTests    rpg.daynight: things come and go out of sight (8); one place at a time (4)
  NightWordsTests  npcs.json's night lines: after the first meeting, never hiding a first meeting, gone by day (20)
  OldSaveTests     a save from before RPG16 (22)
  MapTests         the two maps by the hour: who is where (3), the tool (5), walls (7), out of sight on the map (8), the night
                   foes (9), not a dungeon (10), reachable at every phase (11), the phase note (23), the replay (21), no event
                   on a fight's or an exit's step (18)
  WindowEventTests an event's box through the Adventure window: Yes and No (19), nothing walks behind it (18), morning at the inn (8)
"""
import copy
import os
import random
import statistics
import tempfile
import unittest
import zlib
from unittest import mock

from rpg import clock, conditions, daynight, events, explore, rare, talk
from rpg import enemies
from rpg import adventure as adv
from rpg import save as sv
from tests import test_rpg8 as r8

H = 60


def at(day, hour, minute=0):
    """Game minutes at that day (1 = the first) and hour."""
    return (day - 1) * clock.DAY + hour * H + minute


def save(seed="rpg16-test"):
    """A new save; a word seed is turned into the number a save keeps."""
    return sv.SaveGame(1, "Tester", zlib.crc32(seed.encode()) if isinstance(seed, str) else seed)


class ConditionTests(unittest.TestCase):
    def test_phase_words(self):
        w = {}
        for t, night in ((at(1, 20), True), (at(1, 23, 59), True), (at(2, 4, 59), True), (at(2, 5), False), (at(2, 19, 59), False)):
            with self.subTest(minutes=t):
                self.assertEqual(conditions.holds("phase:night", w, minutes=t), night)
                self.assertEqual(conditions.holds("!phase:night", w, minutes=t), not night)
        self.assertTrue(conditions.holds("phase:dusk|night", w, minutes=at(1, 18, 30)))
        self.assertTrue(conditions.holds("phase:dusk|night", w, minutes=at(1, 21)))
        self.assertFalse(conditions.holds("phase:dusk|night", w, minutes=at(1, 12)))
        self.assertEqual(clock.phase(clock.now({})), "day")                   # no clock: START (17:00) is day
        self.assertTrue(conditions.holds("phase:day", {}))
        self.assertTrue(conditions.holds("phase:night", {"clock": at(1, 22)}))

    def test_problems_name_what_is_wrong(self):
        for cond, word in (("phase:noon", "noon"), ("phase:night|nite", "nite"), ("weather:rain", "unknown condition"),
                           ("quest:the_dark_hall=lost", "quest:<id>"), ("flag:", "names nothing"), ("night", "unknown"),
                           ("!phase:", "names nothing")):
            with self.subTest(cond=cond):
                self.assertIn(word, conditions.problem(cond) or "")
                with self.assertRaises(conditions.ConditionError):
                    conditions.holds(cond, {})
        for cond in ("phase:dawn", "!phase:day|dusk", "flag:met_elder", "!quest:lamplighters_debt=done", "map:warrens_1"):
            self.assertIsNone(conditions.problem(cond), cond)

    def test_flags_quests_and_maps(self):
        w = {"flags": ["met_elder"], "quests": {"the_dark_hall": {"state": "active"}}}
        self.assertTrue(conditions.holds("flag:met_elder", w))
        self.assertTrue(conditions.holds("!flag:met_lamplighter", w))
        self.assertTrue(conditions.holds("quest:the_dark_hall=active", w))
        self.assertTrue(conditions.holds("quest:lamplighters_debt=none", w))
        self.assertTrue(conditions.holds("!quest:the_dark_hall=done", w))
        self.assertTrue(conditions.holds("map:warrens_1", w, place="warrens_1"))
        self.assertFalse(conditions.holds("map:warrens_1", w))                      # no place known: never
        self.assertTrue(conditions.all_hold([], w))
        self.assertEqual(conditions.parse_when("phase:night, !flag:met_elder,"), ["phase:night", "!flag:met_elder"])
        broken = {"flags": "met_elder", "quests": ["x"]}                            # a hand-edited save: read as nothing
        self.assertFalse(conditions.holds("flag:met_elder", broken))
        self.assertTrue(conditions.holds("quest:the_dark_hall=none", broken))

    def test_talk_and_the_hints_know_the_phase_words(self):
        self.assertTrue(talk.holds("phase:night", {"clock": at(1, 22)}))
        with self.assertRaises(talk.TalkError):
            talk.holds("phase:noon", {})
        people = copy.deepcopy(talk.npcs())
        nid = next(iter(people))
        people[nid]["lines"].insert(0, {"when": ["phase:night", "map:lantern_hollow"], "pages": ["Late."]})
        problems = talk.check(people=people)
        self.assertEqual(len([p for p in problems if "unknown condition 'map:lantern_hollow'" in p]), 1, problems)
        self.assertFalse([p for p in problems if "phase:night" in p], problems)


class ClockTests(unittest.TestCase):
    def test_one_key_for_a_whole_phase(self):
        self.assertEqual(clock.phase_key(at(1, 23)), clock.phase_key(at(2, 3)))
        self.assertEqual(clock.phase_key(at(1, 23)), at(1, 20))
        self.assertEqual(clock.phase_key(at(1, 0, 30)), at(0, 20))              # the first night began "the day before"
        self.assertNotEqual(clock.phase_key(at(1, 5, 30)), clock.phase_key(at(2, 5, 30)))
        self.assertEqual(clock.phase_key(at(1, 5, 30)), at(1, 5))
        self.assertEqual(clock.phase_key(at(1, 7)), at(1, 7))
        self.assertEqual(clock.phase_key(at(3, 18, 59)), at(3, 18))
        keys = [clock.phase_key(m) for m in range(0, 3 * clock.DAY, 7)]
        self.assertEqual(keys, sorted(keys))                                    # never goes back
        self.assertEqual(len(set(keys)), 3 * 4 + 1)                              # four phases a day, and the night before day 1

    def test_pass_minutes_never_goes_back(self):
        w = {"clock": at(1, 12)}
        self.assertEqual(clock.pass_minutes(w, 45), at(1, 12, 45))
        for bad in (-30, 0, "x", None, float("nan")):
            with self.subTest(n=bad):
                self.assertEqual(clock.pass_minutes(w, bad), at(1, 12, 45))
        self.assertEqual(clock.pass_minutes({}, 10), clock.START + 10)


class RareTests(unittest.TestCase):
    def kinds(self):
        return {k["id"] for k in enemies.read_kinds()}

    def test_the_table_passes_its_check_and_two_planted_errors_fail(self):
        self.assertEqual(rare.check(kinds=self.kinds()), [])
        bad = copy.deepcopy(rare.spawns())
        bad["red_sash"]["name"] = "The Red Sash"
        bad["old_gorsehide"]["when"] = ["phase:nite"]
        problems = rare.check(bad, kinds=self.kinds())
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("red_sash", problems[1] + problems[0])
        self.assertTrue(any("nite" in p for p in problems), problems)

    def test_the_check_knows_every_field(self):
        bad = {"x": {"kind": "dragon", "encounter": "boss", "when": ["map:warrens_1"], "chance": 1.5, "cooldown_days": -1,
                     "name": "", "home": "h", "taunt": "t", "seen": "s", "reward": {"gold": 0}}}
        text = " | ".join(rare.check(bad, kinds=self.kinds()))
        for word in ("dragon", "encounter", "map word", "chance", "cooldown_days", "no name", "reward"):
            self.assertIn(word, text)

    def test_chance_over_ten_thousand_windows(self):
        s = save()
        entries = {"r": {"when": [], "chance": 0.25, "cooldown_days": 0}}
        n = 10000
        out = sum(rare.is_out(s, "r", minutes=k * clock.DAY / 4 + 1, entries=entries) for k in range(n))
        sd = (n * 0.25 * 0.75) ** 0.5
        self.assertLess(abs(out - n * 0.25), 3 * sd, out)

    def test_it_stays_put_for_the_window_and_after_a_reload(self):
        entries = {"r": {"when": ["phase:night"], "chance": 0.5, "cooldown_days": 0}}
        s = save("stay-put")
        before = s.rng_counter
        windows = [at(d, 20) for d in range(1, 41)]
        first = [rare.is_out(s, "r", minutes=t, entries=entries) for t in windows]
        self.assertTrue(any(first) and not all(first))                          # both answers happen
        for t, f in zip(windows, first):
            for m in (t, t + 61, t + 4 * H, t + 8 * H + 59):                       # anywhere in that night
                self.assertEqual(rare.is_out(s, "r", minutes=m, entries=entries), f)
        self.assertEqual(s.rng_counter, before)                                 # asking never moves the save's counter
        with tempfile.TemporaryDirectory() as folder:
            s.rng("something else")                                             # the counter moved on before saving
            sv.write(s, folder=folder)
            back, _note = sv.read(1, folder=folder)
        self.assertEqual([rare.is_out(back, "r", minutes=t, entries=entries) for t in windows], first)
        self.assertFalse(rare.is_out(s, "r", minutes=at(5, 12), entries=entries))   # by day its when doesn't hold

    def test_cooldown_in_days(self):
        entries = {"r": {"when": [], "chance": 1.0, "cooldown_days": 3}}
        s = save()
        rare.on_beaten(s.world, "r", minutes=at(2, 21))
        self.assertFalse(rare.is_out(s, "r", minutes=at(2, 22), entries=entries))
        self.assertFalse(rare.is_out(s, "r", minutes=at(5, 20, 59), entries=entries))
        self.assertTrue(rare.is_out(s, "r", minutes=at(5, 21), entries=entries))
        self.assertEqual(rare.spot("ashfen_outskirts", "red_sash"), "ashfen_outskirts#rare:red_sash")

    def test_the_shipped_rares_at_their_hours(self):
        s = save()
        w = s.world
        w["flags"] = ["warrens_entered"]
        w["quests"] = {"lamplighters_debt": {"state": "done"}}
        e = rare.spawns()
        with mock.patch.object(rare, "roll", return_value=0.0):               # every roll comes up: only the when decides
            self.assertTrue(rare.is_out(s, "old_gorsehide", minutes=at(1, 19)))
            self.assertTrue(rare.is_out(s, "old_gorsehide", minutes=at(2, 6)))
            self.assertFalse(rare.is_out(s, "old_gorsehide", minutes=at(2, 12)))
            self.assertTrue(rare.is_out(s, "red_sash", minutes=at(1, 23)))
            self.assertTrue(rare.is_out(s, "mother_of_rats", minutes=at(1, 23)))
            w["flags"].append("grave_priest_beaten")
            self.assertFalse(rare.is_out(s, "mother_of_rats", minutes=at(1, 23)))
            self.assertFalse(rare.is_out(s, "no_such_rare", minutes=at(1, 23)))
        self.assertEqual(set(e), {"old_gorsehide", "red_sash", "mother_of_rats"})


class EventTests(unittest.TestCase):
    def maps(self):
        out = {}
        for n in explore.map_names():
            a = explore.load_area(n)
            out[n] = (a.cols, a.rows)
        return out

    def test_the_events_pass_their_check_and_two_planted_errors_fail(self):
        explore.forget_areas()
        self.assertEqual(events.check(maps=self.maps(), wrap=talk.wrap, page_lines=talk.PAGE_LINES), [])
        bad = copy.deepcopy(events.events())
        bad["stuck_cart"]["where"] = [0, 17, 60, 22]                             # one column past the map's edge
        bad["the_wisp"]["yes"]["pages"][0] += " And on, and on, much further than any one page of the dialogue box could hold."
        problems = events.check(bad, maps=self.maps(), wrap=talk.wrap, page_lines=talk.PAGE_LINES)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("stuck_cart", problems[0])
        self.assertIn("the_wisp yes", problems[1])

    def test_the_check_knows_every_field(self):
        bad = {"a": {"map": "nowhere", "when": ["phase:noon", "flag:never_set"], "weight": 0, "pages": [],
                     "effect": {"gold": -5, "card": "mythic", "minutes": 5000, "colour": "red"}},
               "b": {"title": "B", "map": "lantern_hollow", "cooldown_days": 1, "pages": ["x"], "choice": ["Yes"],
                     "effect": {"gold": 1}, "yes": {"pages": ["y"], "spell": 1}}}
        text = " | ".join(events.check(bad, maps=self.maps(), known_flags={"met_elder"}))
        for word in ("no title", "no map 'nowhere'", "noon", "never_set", "weight", "cooldown_days", "says nothing", "gold -5",
                     "card 'mythic'", "minutes 5000", "unknown effect", "two answers", "go in its yes and no", "no no",
                     "unknown ['spell']"):
            self.assertIn(word, text)

    def test_the_gap_between_checks(self):
        s = save()
        rate = 500
        gaps = [events.next_gap(s, rate) for _ in range(10000)]
        self.assertGreaterEqual(min(gaps), events.EVENT_MIN_GAP)
        self.assertLessEqual(max(gaps), 2 * rate - events.EVENT_MIN_GAP)
        self.assertLess(abs(statistics.mean(gaps) - rate), 0.02 * rate)

    def walk(self, seed, days, map_name="ashfen_outskirts", rate=500):
        """A walker who never stops, on random tiles of one map, for `days` game days (4.5 tiles a second, one game minute a
        second): every event that fires, with the world as it was."""
        s = save(seed)
        w = s.world
        w["clock"] = at(1, 0)
        rng = random.Random(seed)
        cols, rows = self.maps()[map_name]
        fired = []
        steps = int(days * clock.DAY * 4.5)
        for _ in range(steps):
            clock.advance(w, 1 / 4.5)
            tile = (rng.randrange(cols), rng.randrange(rows))
            if clock.now(w) >= at(days // 2 + 1, 0) and "grave_priest_beaten" not in (w.get("flags") or []):
                w.setdefault("flags", []).append("grave_priest_beaten")          # half way: the bell stops ringing
            eid = events.step(s, map_name, tile, rate)
            if eid:
                fired.append((eid, clock.now(w), tile, copy.deepcopy(w)))
                events.fired(w, eid)
        return s, fired

    def test_fifty_game_days_keep_every_rule(self):
        e = events.events()
        s, fired = self.walk("fifty-days", 50)
        self.assertGreater(len(fired), 20)
        last = {}
        for eid, t, tile, world in fired:
            ev = e[eid]
            with self.subTest(event=eid, minutes=t):
                self.assertEqual(ev["map"], "ashfen_outskirts")
                self.assertTrue(events.in_box(ev.get("where"), tile))
                self.assertTrue(conditions.all_hold(ev.get("when"), world, minutes=t))
                if eid in last:
                    self.assertGreaterEqual(t - last[eid], ev["cooldown_days"] * clock.DAY)
                last[eid] = t
        self.assertEqual({eid for eid, *_ in fired}, {"stuck_cart", "the_wisp", "chapel_bell"})

    def test_a_once_event_happens_once(self):
        entries = {"o": {"map": "lantern_hollow", "once": True, "weight": 1, "pages": ["x"], "effect": {"flag": "f"}}}
        s = save()
        got = []
        for _ in range(5000):
            eid = events.step(s, "lantern_hollow", (5, 5), 300, entries=entries)
            if eid:
                got.append(eid)
                events.fired(s.world, eid, entries=entries)
        self.assertEqual(got, ["o"])

    def test_no_event_on_a_map_without_a_rate(self):
        s = save()
        self.assertTrue(all(events.step(s, "ashfen_outskirts", (5, 20), 0) is None for _ in range(2000)))

    def test_effects_land_in_the_save(self):
        class Game:
            pass
        g = Game()
        g.save = save()
        g.save.gold = 100
        g.save.world["clock"] = at(1, 12)
        lines = events.apply(g, "stuck_cart", "yes")
        self.assertEqual((g.save.gold, clock.now(g.save.world)), (115, at(1, 12, 45)))
        self.assertEqual(lines, ["+15 gold."])
        self.assertEqual(events.apply(g, "stuck_cart", "no"), [])
        self.assertEqual(g.save.gold, 115)
        events.apply(g, "chapel_bell")
        self.assertIn("heard_the_bell", g.save.world["flags"])
        with mock.patch("rpg.adventure.give_reward", return_value=["New card: X."]) as gr:
            self.assertEqual(events.apply(g, "well_offering", "yes"), ["New card: X."])
        gr.assert_called_once_with(g, {"card": "rare_from_band"})
        self.assertIn("took_the_offering", g.save.world["flags"])
        self.assertEqual(events.pages_of(events.events()["stuck_cart"], "no"), ["He shouts something about scribes. It isn't kind."])
        self.assertIn("heard_the_bell", events.flags_set())


class RareTrigger:
    """A rare foe's trigger as rpg.explore makes it: the kind's enemy, its rare id, a spot of its own."""
    kind = "encounter"
    once = False
    random = False

    def __init__(self, rid, map_name="ashfen_outskirts"):
        e = rare.spawns()[rid]
        self.rare, self.enemy, self.encounter = rid, e["kind"], e["encounter"]
        self.spot = rare.spot(map_name, rid)


def map_trigger(encounter="regular"):
    for area_name in explore.map_names():
        for t in explore.load_area(area_name).triggers:
            if t.kind == "encounter" and t.encounter == encounter:
                return t
    raise AssertionError("no such encounter")


class RareDuelTests(r8.Base):
    def test_the_fight_question_names_it_its_reward_and_its_cooldown(self):
        g = r8.new_game(None)
        title, lines = adv.fight_text(g, RareTrigger("old_gorsehide"))
        self.assertTrue(title.startswith("Old Gorsehide"), title)
        self.assertTrue(lines[0].startswith("Win: ") and lines[0].endswith(", and 40 gold."), lines[0])
        self.assertIn("Beaten, it stays away for 2 days.", lines)       # Round RPG17 (expected change, Karl 24 Oct Q6): was 3
        self.assertTrue(adv.fight_text(g, RareTrigger("red_sash"))[1][0].endswith(", and a rare card."))
        self.assertIsNone(adv.rare_entry(map_trigger()))                  # an ordinary foe is no rare

    def test_the_encounter_screen_shows_name_home_taunt_and_rare(self):
        import pygame
        import rpg_encounter as renc
        pygame.init()
        pygame.display.set_mode((64, 64))
        g = r8.new_game(None)
        t = RareTrigger("mother_of_rats")
        e = rare.spawns()["mother_of_rats"]
        scr = renc.EncounterScreen(g, t, 0.0, animations=False)
        self.assertEqual((scr.name, scr.text["home"], scr.text["taunt"]), (e["name"], e["home"], e["taunt"]))
        plain = renc.EncounterScreen(g, map_trigger("boss"), 0.0, animations=False)
        self.assertIsNone(plain.rare)
        self.assertIsNotNone(scr.foe)
        self.assertEqual(scr.foe_scale, renc.foe_layout(scr.kd, False, 1)[1])          # as big as fits, one step at most
        big = renc.EncounterScreen(g, RareTrigger("old_gorsehide"), 0.0, animations=False)
        self.assertEqual(big.foe_scale, renc.foe_layout(big.kd, False)[1] + 1)           # one step bigger than the kind itself
        drawn = []
        real = renc.rpg_ui.text

        def spy(c, s, *a, **k):
            drawn.append(s)
            return real(c, s, *a, **k)
        with mock.patch.object(renc.rpg_ui, "text", spy):
            scr.canvas(10.0)
        self.assertIn("RARE", drawn)
        self.assertTrue(any(e["name"].startswith(d) for d in drawn if d), drawn[:6])

    def test_a_win_pays_its_reward_and_starts_its_cooldown_and_a_loss_does_neither(self):
        g = r8.new_game(None)
        g.save.world["clock"] = at(1, 22)
        g.write()
        gold = g.save.gold
        adv.start_duel(g, RareTrigger("old_gorsehide"))
        self.assertEqual(r8.on_disk(self.folder)["pending_duel"]["rare"], "old_gorsehide")
        out = adv.finish_duel(1, "won")
        disk = r8.on_disk(self.folder)
        self.assertEqual(disk["world"]["rare"]["old_gorsehide"]["beaten_at"], at(1, 22))
        self.assertEqual(disk["gold"], gold + out["gold"] + 40)
        lines = adv.outcome_lines(out)
        self.assertEqual(lines[0], f"You beat the Old Gorsehide: +{out['gold']} gold.")
        self.assertIn("+40 gold.", lines)
        g = r8.new_game(None)
        gold = g.save.gold
        adv.start_duel(g, RareTrigger("old_gorsehide"))
        out = adv.finish_duel(1, "lost")
        disk = r8.on_disk(self.folder)
        self.assertNotIn("rare", disk["world"])
        self.assertEqual(disk["gold"], gold - g.numbers.loss_cost(gold, "dungeon"))   # GOLD1: its encounter's share
        self.assertEqual(adv.outcome_lines(out, g.world)[0], "The Old Gorsehide beat you.")


class PresenceTests(unittest.TestCase):
    def test_a_night_foe_appears_only_out_of_sight_and_leaves_only_out_of_sight(self):
        rat = daynight.Item("ashfen_outskirts#night_rat_1", ["phase:night"], (30, 10))
        p = daynight.Presence([rat, daynight.Item("ashfen_outskirts#3", [], (5, 5))])
        w = {}
        in_view, away = (20, 2, 40, 18), (0, 20, 15, 35)
        self.assertEqual(p.update(w, at(1, 19, 50), in_view), [])                   # the first update sets things (day: no rat)
        self.assertTrue(p.is_present("ashfen_outskirts#3"))
        self.assertEqual(p.update(w, at(1, 20, 5), in_view), [])                    # night, but its home is in view
        self.assertFalse(p.is_present(rat.key))
        self.assertEqual(p.update(w, at(1, 20, 6), away), [rat.key])                # looked away: it is there
        self.assertEqual(p.update(w, at(2, 5, 1), in_view), [])                     # dawn, in view: it stays
        self.assertTrue(p.is_present(rat.key))
        self.assertEqual(p.update(w, at(2, 5, 2), away), [rat.key])
        self.assertFalse(p.is_present(rat.key))

    def test_never_on_the_walker_and_force_sets_all_at_once(self):
        rat = daynight.Item("r", ["phase:night"], (30, 10))
        p = daynight.Presence([rat])
        p.update({}, at(1, 12), None)
        self.assertEqual(p.update({}, at(1, 21), None, walker_tile=(30, 10)), [])   # out of view, but you stand there
        self.assertEqual(p.update({}, at(1, 21), (0, 0, 40, 40), force=True), ["r"])
        p2 = daynight.Presence([rat])
        self.assertEqual(p2.update({}, at(1, 21), (0, 0, 40, 40)), ["r"])           # a fresh map: everything at once

    def test_one_place_at_a_time_and_always_somewhere(self):
        day, night = ["!phase:night"], ["phase:night"]
        self.assertEqual(daynight.clashes([day, night]), [])
        self.assertEqual(daynight.gaps([day, night]), [])
        self.assertTrue(daynight.clashes([[], night]))                              # plant 2: a day marker without `when`
        self.assertTrue(daynight.gaps([["phase:day"], night]))                      # nobody at dawn and dusk
        pell = [["!phase:dusk|night"], ["phase:dusk|night", "!flag:grave_priest_beaten"], ["phase:dusk|night", "flag:grave_priest_beaten"]]
        self.assertEqual((daynight.clashes(pell), daynight.gaps(pell)), ([], []))
        self.assertIn("night, grave_priest_beaten", daynight.clashes([["phase:night"], ["flag:grave_priest_beaten"]]))


class NightWordsTests(unittest.TestCase):
    def test_night_lines_follow_the_first_meeting_and_fit(self):
        self.assertEqual(talk.check(), [])
        people = talk.npcs()
        for nid, n in people.items():
            lines = n["lines"]
            night = [i for i, e in enumerate(lines) if "phase:night" in (e.get("when") or [])]
            if not night:
                continue
            first = [i for i, e in enumerate(lines) if f"!flag:met_{nid}" in (e.get("when") or [])]
            with self.subTest(nid=nid):
                self.assertTrue(first and max(first) < min(night), (first, night))

    def test_who_says_what_by_hour(self):
        met = {"flags": [f"met_{n}" for n in talk.npcs()],
               "quests": {"the_dark_hall": {"state": "done"}, "lamplighters_debt": {"state": "done"}}}   # givers' night words wait
        for nid in ("innkeeper", "card_seller", "pack_seller", "hall_keeper", "lamplighter", "elder", "platino"):
            with self.subTest(nid=nid):
                day = talk.entry_for(nid, dict(met, clock=at(1, 12)))[1]
                night = talk.entry_for(nid, dict(met, clock=at(1, 22)))[1]
                first_at_night = talk.entry_for(nid, {"flags": [], "clock": at(1, 22)})[1]
                first_by_day = talk.entry_for(nid, {"flags": [], "clock": at(1, 12)})[1]
                if nid == "elder":                                      # while his quest is open his words are the quest's
                    self.assertEqual(talk.entry_for(nid, {"flags": ["met_elder"], "clock": at(1, 22)})[1],
                                     talk.entry_for(nid, {"flags": ["met_elder"], "clock": at(1, 12)})[1])
                self.assertNotEqual(day, night)
                self.assertEqual(first_at_night, first_by_day)          # a first meeting at night gets the first-meeting words


class OldSaveTests(unittest.TestCase):
    def test_a_save_from_before_rpg16_plays(self):
        s = save()
        self.assertNotIn("rare", s.world)
        self.assertNotIn("events", s.world)
        self.assertIsNone(rare.beaten_at(s.world, "red_sash"))
        rare.is_out(s, "red_sash", minutes=at(1, 23))
        events.step(s, "ashfen_outskirts", (5, 20), 500)
        self.assertIsInstance(s.world["events"]["gap"], (int, float))

    def test_broken_values_are_treated_as_absent(self):
        s = save()
        for bad in ("x", 5, None, ["gap"], {"gap": "soon", "last": [], "done": "all"}, {"gap": -3, "last": {"a": "noon"}}):
            s.world["events"] = copy.deepcopy(bad)
            st = events.state(s.world)
            with self.subTest(events=bad):
                self.assertTrue(st["gap"] is None and isinstance(st["last"], dict) and isinstance(st["done"], list))
                self.assertFalse(any(not isinstance(v, (int, float)) for v in st["last"].values()))
        for bad in ("x", {"red_sash": 5}, {"red_sash": {"beaten_at": "yesterday"}}, {"red_sash": {"beaten_at": float("inf")}}):
            s.world["rare"] = bad
            self.assertIsNone(rare.beaten_at(s.world, "red_sash"))
        s.world["rare"] = "x"
        rare.on_beaten(s.world, "red_sash", minutes=100)
        self.assertEqual(s.world["rare"], {"red_sash": {"beaten_at": 100}})


if __name__ == "__main__":
    unittest.main()


# ---- the maps by the hour ------------------------------------------------------------------------------------------------------
TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
DAWN_PEOPLE = {"card_seller": (31, 4), "elder": (17, 16), "gate_guard": (35, 15), "hall_keeper": (20, 24), "innkeeper": (10, 4),
               "lamplighter": (23, 12), "pack_seller": (7, 24), "platino": (1, 28)}
WHO_IS_WHERE = {                                                   # the spec's section 3.1, phase by phase
    6: DAWN_PEOPLE,
    12: DAWN_PEOPLE,
    19: dict(DAWN_PEOPLE, lamplighter=(22, 18)),
    21: {"card_seller": (31, 4), "elder": (32, 21), "hall_keeper": (20, 24), "innkeeper": (10, 4), "lamplighter": (22, 18),
         "mourner": (20, 15), "night_watch": (35, 16), "pack_seller": (7, 24), "platino": (1, 28)},
}
ALWAYS_OUT = {"ashfen_outskirts#3", "ashfen_outskirts#6", "ashfen_outskirts#9", "ashfen_outskirts#10", "ashfen_outskirts#12",
              "ashfen_outskirts#13"}                              # the bandits, thornbacks and the grave robber of RPG13
ZEALOT = "ashfen_outskirts#11"
NIGHT = {"ashfen_outskirts#night_graves", "ashfen_outskirts#night_chapel"}
RATS = {"ashfen_outskirts#night_rocks", "ashfen_outskirts#night_mill"}


def explorer_at(map_name, minutes, flags=(), quests=None, seed="rpg16-map"):
    s = save(seed)
    s.world.update(map=map_name, clock=minutes, flags=list(flags))
    if quests:
        s.world["quests"] = {q: {"state": st} for q, st in quests.items()}
    return explore.Explorer(s)


def place(ex, tile):
    """The walker on `tile`'s middle (as a new map would put it: everything set at once)."""
    from tests import rpg_route
    a = ex.area
    ex._use(a, explore.Walker(a, *rpg_route.middle(a, tile)))
    return ex


def walker_tile(ex):
    return ex.area.tile_at(ex.walker.x, ex.walker.y)


def press(ex, d, frames, clock_runs=True):
    """Hold a direction; the clock runs while the walker moves, as rpg_screens.walk_tick has it. Returns the events."""
    from tests import rpg_route
    out = []
    for _ in range(frames):
        e = ex.step(d[0], d[1], rpg_route.FRAME)
        if clock_runs and ex.walker.moving:
            clock.advance(ex.save.world, rpg_route.FRAME)
        if e:
            out.append(e)
    return out


class MapTests(unittest.TestCase):
    def test_who_is_where_by_hour(self):
        """Spec test 3: the people out in Lantern Hollow at 06:00, 12:00, 19:00 and 21:00 are exactly the spec's table."""
        for hour, want in WHO_IS_WHERE.items():
            ex = explorer_at(TOWN, at(1, hour))
            with self.subTest(hour=hour):
                got = {}
                for p in ex.present_people():
                    self.assertNotIn(p["id"], got, f"{p['id']} is in two places at {hour}:00")
                    got[p["id"]] = p["tile"]
                self.assertEqual(got, want)

    def test_the_tool_leaves_scheduled_people_off_the_people_layer(self):
        """Spec test 5: a person with `when` is drawn by the map view, not on the people layer (the layer would wall them in at
        every hour); one without still is; home= gets its spaces; the shipped maps are what the tool builds."""
        from tests import test_rpg12 as r12
        tool = r12.make_tool()
        src = os.path.join(explore.MAPS_DIR, "src")
        lay = tool.read_layout(os.path.join(src, TOWN + ".txt"))
        w, _h, layers, objects = tool.build(lay, tool.Sheets())
        people = layers["people"]
        a = explore.load_area(TOWN)
        for p in a.people:
            with self.subTest(person=p["id"], tile=p["tile"]):
                c, r = p["tile"]
                if p["when"]:
                    self.assertEqual(people[r * w + c], 0, "a person who comes and goes is on the people layer")
                    self.assertIsNotNone(p["cell"], "no sprite cell for the map view to draw them with")
                else:
                    self.assertNotEqual(people[r * w + c], 0)
        yard = next(o for o in objects if o[0] == "encounter" and o[1].get("spot") == "night_yard")
        self.assertEqual(yard[1]["home"], "The back yards of Lantern Hollow")
        self.assertEqual(tool.main(["--check"]), 0)

    def test_people_who_come_and_go_are_walls_only_while_there(self):
        """Spec test 7: Wenna (by the well, at night) stops the walker; by day the same tile is walked onto; she never appears on
        the walker."""
        well = (20, 15)
        ex = place(explorer_at(TOWN, at(1, 22)), (19, 15))
        self.assertIn(well, ex.walker.blockers)
        press(ex, (1, 0), 40, clock_runs=False)
        self.assertEqual(walker_tile(ex), (19, 15), "walked through Wenna")
        ex = place(explorer_at(TOWN, at(1, 12)), (19, 15))
        self.assertNotIn(well, ex.walker.blockers)
        self.assertFalse(ex.area.blocked(*well))
        tiles = {walker_tile(ex)}
        for _ in range(40):
            press(ex, (1, 0), 1, clock_runs=False)
            tiles.add(walker_tile(ex))
        self.assertIn(well, tiles, "by day the well's tile is free")
        ex = place(explorer_at(TOWN, at(1, 19, 59)), well)            # standing where she will be when night falls
        ex.save.world["clock"] = at(1, 20, 30)
        ex.view_half = (0, 0)
        ex.refresh()
        self.assertFalse(ex.presence.is_present(next(p["key"] for p in ex.area.people if p["id"] == "mourner")))

    def test_night_foes_come_and_go_out_of_sight_on_the_map(self):
        """Spec test 8: at 20:00 a night foe whose home is in view isn't there until the view leaves it; at 05:00 one in view stays
        until out of view; entering the map sets everything at once."""
        graves = (21, 30)
        ex = place(explorer_at(OUT, at(1, 19, 58)), (21, 27))
        spot = "ashfen_outskirts#night_graves"
        self.assertFalse(ex.is_out(spot))
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        self.assertFalse(ex.is_out(spot), "a night foe appeared in view")
        ex.view_half = (0, 0)                                        # the view as small as it gets: its home is out of view
        far = place(explorer_at(OUT, at(1, 20, 1)), (21, 27))       # a new map: everything at once
        self.assertTrue(far.is_out(spot))
        ex.walker.x += 40 * ex.area.tw                               # looked away
        ex.refresh()
        self.assertTrue(ex.is_out(spot))
        ex.walker.x -= 40 * ex.area.tw
        ex.view_half = explore.VIEW_HALF
        ex.save.world["clock"] = at(2, 5, 1)
        ex.refresh()
        self.assertTrue(ex.is_out(spot), "it left while in view")
        ex.walker.x += 40 * ex.area.tw
        ex.view_half = (0, 0)
        ex.refresh()
        self.assertFalse(ex.is_out(spot))
        self.assertEqual(graves, ex.area.tile_at(*ex.foes[spot].home))

    def test_the_night_foes_by_flag_state(self):
        """Spec test 9: the outskirts' foes at 21:00 by flag state (rares aside: they depend on their roll); by day; the regular
        kinds are still the four of RPG9."""
        def out(minutes, flags=()):
            ex = explorer_at(OUT, minutes, flags)
            return {f.spot for f in ex.encounters() if not f.t.rare}
        self.assertEqual(out(at(1, 12)), ALWAYS_OUT | {ZEALOT})
        self.assertEqual(out(at(1, 21)), ALWAYS_OUT | NIGHT)                                    # 8
        self.assertEqual(out(at(1, 21), ["warrens_entered"]), ALWAYS_OUT | NIGHT | RATS)        # 10
        self.assertEqual(out(at(1, 21), ["warrens_entered", "grave_priest_beaten"]), ALWAYS_OUT | NIGHT)
        self.assertEqual(enemies.regular_kinds(), ["ember_bandit", "grave_robber", "thornback", "zealot"])
        rat = explore.load_area(OUT)
        rats = [t for t in rat.triggers if t.spot in RATS]
        self.assertTrue(rats and all(t.encounter == "dungeon" and not t.once for t in rats))

    def test_the_town_and_the_outskirts_are_not_dungeons(self):
        """Spec test 10: the rats' dungeon fights don't make the outskirts a dungeon: its backdrop, and its own music and ambience
        (RPG15's map properties: the outskirts' ambience is the graveyard's by choice), never the Warrens'."""
        import rpg_screens as rs
        cave = explore.load_area("warrens_1")
        for name in (TOWN, OUT):
            a = explore.load_area(name)
            with self.subTest(map=name):
                self.assertFalse(a.dungeon)
                self.assertEqual(a.backdrop, "forest")
                flow = rs.AdventureFlow.__new__(rs.AdventureFlow)
                flow.explorer, flow.stage, flow.sub, flow.fight = explorer_at(name, at(1, 21), ["warrens_entered"]), "map", None, None
                amb, music = flow.soundscape()
                # Round RPG-NM (expected change): at 21:00 the map's night music (Karl's night picks, 25 Oct), was its day music
                self.assertEqual((amb, music), (a.ambience, "loop:" + a.night_music))
                self.assertNotEqual(a.night_music, cave.music)
                self.assertNotEqual(music, "loop:" + cave.music)

    def test_everything_is_reachable_at_every_hour(self):
        """Spec test 11: with each hour's people as walls, every spawn, exit, town, foe and rare spot can be reached and everyone
        out can be talked to; and with each new foe's home and the tiles round it walled too (a foe standing there), still."""
        for name in (TOWN, OUT):
            for hour in (6, 12, 19, 21):
                ex = explorer_at(name, at(1, hour), ["warrens_entered"], {"lamplighters_debt": "done"})
                a = ex.area
                walls = set(ex.walker.blockers)
                new_homes = [a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
                             for t in a.triggers if t.kind == "encounter" and (t.when or t.rare)]
                rings = [set()] + [{(c + dc, r + dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1)} for c, r in new_homes]
                for ring in rings:
                    with self.subTest(map=name, hour=hour, ring=sorted(ring)[:1]):
                        reach = a.reachable(a.spawn(), walls | ring)
                        self.assertTrue(reach)
                        for sname, xy in a.spawns.items():
                            t = a.tile_at(*xy)
                            if t not in ring and not a.blocked(*t):
                                self.assertIn(t, reach, f"spawn {sname}")
                        for t in a.triggers:
                            tile = a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
                            if tile in ring:
                                continue
                            self.assertTrue(a._reachable_box(t.box, reach) or (t.kind == "town" and a._beside_counter(t.box, reach)),
                                            f"{t.kind} {t.spot or t.obj.id} at {tile}")
                        for p in ex.present_people():
                            c, r = p["tile"]
                            near = [(c + dc, r + dr) for dc, dr in ((0, 1), (0, -1), (1, 0), (-1, 0))]
                            near += [(c + 2 * dc, r + 2 * dr) for dc, dr in ((0, 1), (0, -1), (1, 0), (-1, 0))
                                     if a.is_counter(c + dc, r + dr)]
                            self.assertTrue(any(t in reach for t in near), f"{p['id']} can't be talked to")

    def test_the_phase_note_once_outdoors_and_never_in_the_warrens(self):
        """Spec test 23: crossing 20:00 on the outskirts says "Night falls." once; crossing it in the Warrens says nothing."""
        ex = explorer_at(OUT, at(1, 19, 59))
        ex.take_notes()
        ex.save.world["clock"] = at(1, 20, 0) + 0.5
        ex.refresh()
        self.assertEqual(ex.take_notes(), ["Night falls."])
        ex.refresh()
        ex.save.world["clock"] = at(1, 20, 5)
        ex.refresh()
        self.assertEqual(ex.take_notes(), [])
        cave = explorer_at("warrens_1", at(1, 19, 59))
        cave.take_notes()
        cave.save.world["clock"] = at(1, 20, 1)
        cave.refresh()
        self.assertEqual(cave.take_notes(), [])
        self.assertEqual(explore.phase_note("dawn"), "Dawn. The moor goes grey.")

    def test_a_scripted_walk_replays_the_same(self):
        """Spec test 21: one walk (the same keys and times) from one save, twice: the same events, foes met, rares, notes and clock."""
        from tests import rpg_route

        def run():
            ex = explorer_at(OUT, at(1, 19, 58), ["warrens_entered"], {"lamplighters_debt": "done"}, seed="replay")
            ex.save.world["events"] = {"gap": 60}                  # the first check soon (after nightfall), so the walk meets one
            a = ex.area
            free = sorted(a.reachable(a.spawn(), ex.walker.blockers) - rpg_route.trigger_tiles(a))
            goals = random.Random(21).sample(free, 14)
            log = []
            frame_no = 0
            for goal in goals:
                for _ in range(1500):
                    if ex.area.name != OUT:                        # through an exit: back on the outskirts, by its gate
                        b = explore.load_area(OUT)
                        ex._use(b, explore.Walker(b, *b.spawn("from_town")))
                    try:
                        d = rpg_route.next_press(ex, goal)
                    except AssertionError:                         # walled off by a foe or a person for now
                        d = (0, 1)
                    if d is None:
                        break
                    frame_no += 1
                    for e in press(ex, d, 1):
                        log.append((frame_no, e[0], getattr(e[1], "spot", getattr(e[1], "kind", e[1]))))
                    log += [(frame_no, "note", n) for n in ex.take_notes()]
            w = ex.save.world
            return log, round(clock.now(w), 3), copy.deepcopy(w.get("events")), copy.deepcopy(w.get("rare")), \
                sorted(ex.presence.present), walker_tile(ex)
        first, second = run(), run()
        self.assertEqual(first, second)
        kinds = {k for _i, k, _w in first[0]}
        self.assertTrue({"event", "note", "encounter"} <= kinds, kinds)

    def test_no_event_check_on_a_step_a_fight_or_an_exit_happened(self):
        """Spec test 18 (on the map): the event gap is counted only on a step nothing else happened on."""
        from tests import rpg_route
        real = events.step
        calls = []
        with mock.patch.object(explore.rpg_events, "step", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
            for goal in ("exit", "encounter"):
                ex = explorer_at(OUT, at(1, 12))
                a = ex.area
                if goal == "exit":
                    t = next(t for t in a.triggers if t.kind == "exit")
                    target = a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
                else:
                    t = next(t for t in a.triggers if t.spot == "ashfen_outskirts#10")
                    target = lambda: a.tile_at(ex.foes[t.spot].x, ex.foes[t.spot].y)
                happened = None
                for _ in range(6000):
                    here = walker_tile(ex)
                    tg = target() if callable(target) else target
                    route = rpg_route.tile_route(a, here, tg, avoid=(rpg_route.trigger_tiles(a) - {tg}) | set(ex.walker.blockers))
                    d = rpg_route.next_press(ex, tg) if route else (1, 0)
                    n = len(calls)
                    e = ex.step(d[0], d[1], rpg_route.FRAME)
                    if e and e[0] in ("map", "encounter"):
                        self.assertEqual(len(calls), n, f"an event was checked on the {e[0]} step")
                        happened = e[0]
                        break
                with self.subTest(goal=goal):
                    self.assertIn(happened, ("map", "encounter"))
                    self.assertTrue(calls, "no step counted the gap at all")



# ---- events through the window ---------------------------------------------------------------------------------------------------
def _window_base():
    from tests import test_rpg14 as r14
    return r14.WindowBase


class WindowEventTests(_window_base()):
    def night_on(self, g, name, spawn, hour=22):
        f = self.on_map(g, name, spawn)
        f.game.save.world["clock"] = at(1, hour)
        f.explorer.refresh(force=True)
        return f

    def test_each_answer_says_its_own_pages_and_gives_its_own_effects(self):
        """Spec test 19 through the window: the box says the event's pages, then the answer's; Yes pays and passes time, No
        gives nothing; the event's cooldown starts either way."""
        e = events.events()["the_wisp"]
        for answer in (True, False):
            g, _l = self.setup_game()
            f = self.night_on(g, OUT, "from_town")
            w = f.game.save.world
            gold, t0 = f.game.save.gold, clock.now(w)
            f.start_event(g, "the_wisp")
            self.assertEqual((f.sub, f.talk_box.name), ("talk", e["title"]))
            self.assertEqual(f.talk_box.choice, tuple(e["choice"]))
            seen = self.say_all(g, answer=answer)
            with self.subTest(answer=answer):
                self.assertIsNone(f.sub)
                self.assertEqual(seen[:len(e["pages"])], e["pages"])
                part = e["yes" if answer else "no"]
                self.assertEqual(seen[len(e["pages"]):len(e["pages"]) + len(part["pages"])], part["pages"])
                self.assertEqual(f.game.save.gold, gold + (25 if answer else 0))
                self.assertAlmostEqual(clock.now(w), t0 + (90 if answer else 0), places=2)
                self.assertEqual(w["events"]["last"]["the_wisp"], round(t0, 3))
                if answer:
                    self.assertIn("+25 gold.", seen)
                disk = r8.on_disk(self.folder)
                self.assertEqual(disk["gold"], f.game.save.gold)

    def test_an_event_without_a_choice_gives_its_effect_when_the_box_closes(self):
        g, _l = self.setup_game()
        f = self.night_on(g, OUT, "from_town")
        f.start_event(g, "chapel_bell")
        self.assertIsNone(f.talk_box.choice)
        self.assertNotIn("heard_the_bell", talk.flags(f.game.save.world))
        self.assertEqual(self.say_all(g), events.events()["chapel_bell"]["pages"])
        self.assertIn("heard_the_bell", talk.flags(f.game.save.world))
        self.assertIn("heard_the_bell", r8.on_disk(self.folder)["world"]["flags"])

    def test_walking_on_a_night_moor_meets_an_event_and_nothing_walks_while_it_is_open(self):
        """Spec test 18 (the window): an event opens from walking; while its box is open the walker doesn't move and the gap isn't
        counted."""
        from tests.test_forge_table import frame
        g, _l = self.setup_game()
        f = self.night_on(g, OUT, "from_town")
        w = f.game.save.world
        w["events"] = {"gap": 3}
        t = 100.0
        for i in range(2000):
            if f.sub == "talk":
                break
            t += 1 / 60
            d = (1, 0) if (i // 90) % 2 == 0 else (-1, 0)
            f.walk_tick(g, t, pressed=d)
            if f.sub == "fight":
                self.fail("walked into a foe before the event")
        self.assertEqual(f.sub, "talk", "no event in 2000 frames with the gap at 3 tiles")
        self.assertIsNotNone(f.talk_event)
        eid = f.talk_event[0]
        self.assertIn(eid, events.eligible(w, OUT, explore_tile(f.explorer), minutes=clock.now(w)) + [eid])
        before = (f.explorer.walker.x, f.explorer.walker.y, w["events"]["gap"], clock.now(w))
        for _ in range(120):
            t += 1 / 60
            f.walk_tick(g, t, pressed=(1, 0))
        frame(g)
        self.assertEqual((f.explorer.walker.x, f.explorer.walker.y, w["events"]["gap"], clock.now(w)), before)

    def test_a_night_at_the_inn_is_morning_at_once(self):
        """Spec test 8 (waking): the night people are gone and the day people back the moment you wake, in view or not, and the
        map says so."""
        from tests.test_forge_table import key
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 22)
        f.explorer.refresh(force=True)
        out = {p["id"] for p in f.explorer.present_people()}
        self.assertTrue({"mourner", "night_watch"} <= out and "gate_guard" not in out, out)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame_key("K_RETURN"))
        self.say_all(g, answer=True)                                  # Round RPG15c: "Sleep until morning"
        w = f.game.save.world
        self.assertEqual(clock.now(w), at(2, 7))
        out = {p["id"] for p in f.explorer.present_people()}
        self.assertTrue("gate_guard" in out and not ({"mourner", "night_watch"} & out), out)
        self.assertEqual(f.message[0], "Morning.")


def explore_tile(ex):
    return ex.area.tile_at(ex.walker.x, ex.walker.y)


def pygame_key(name):
    import pygame
    return getattr(pygame, name)



class IdTests(unittest.TestCase):
    """Spec test 6: every object id RPG15b shipped keeps its meaning (tests/fixtures/rpg/object_ids_rpg15b.json, RPG15c's pin);
    the four that gained a `when` are exactly the spec's, and the tool keeps an id when an object only gains a property."""

    def test_only_the_specs_four_objects_gained_a_when(self):
        import json
        import xml.etree.ElementTree as ET
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "tests", "fixtures", "rpg", "object_ids_rpg15b.json"), encoding="utf-8") as fh:
            old = json.load(fh)["maps"]
        gained = {}
        for name, obs in old.items():
            for ob in ET.parse(os.path.join(explore.MAPS_DIR, name + ".tmx")).getroot().iter("object"):
                oid = ob.get("id")
                if oid in obs:
                    props = {p.get("name"): p.get("value") for p in ob.iter("property")}
                    extra = {k: v for k, v in props.items() if k not in obs[oid]["props"]}
                    if extra:
                        gained[f"{name}#{oid}"] = (ob.get("name"), extra)
        self.assertEqual(gained, {
            "ashfen_outskirts#11": ("", {"when": "!phase:night"}),                       # the zealot, by day
            "lantern_hollow#7": ("Pell the lamplighter", {"when": "!phase:dusk|night"}),
            "lantern_hollow#10": ("Gate guard", {"when": "!phase:night"}),
            "lantern_hollow#11": ("Elder Hesk", {"when": "!phase:night"}),
            # Round RPG18 (expected change, Karl 24 Oct, Plan 2): Pell's three bandits leave once his quest is ready or done
            # Round RPG18c (expected change, Karl 26 Oct): ... and once one of them is beaten the other flees
            **{f"ashfen_outskirts#{i}": ("", {"when": "!quest:lamplighters_debt=ready,!quest:lamplighters_debt=done,"
                                                      "!flag:road_bandits_fled"})
               for i in (9, 10)},
            # Round RPG18b (expected change, Karl 25 Oct): the one by the bridge is the bandits' leader - his own name
            "ashfen_outskirts#6": ("", {"when": "!quest:lamplighters_debt=ready,!quest:lamplighters_debt=done",
                                        "title": "Ember Bandit Leader"}),
        })

    def test_the_tool_keeps_an_id_when_an_object_only_gains_a_property(self):
        from tests import test_rpg12 as r12
        tool = r12.make_tool()
        objs = [("encounter", {"kind": "zealot"}, (320, 160, 32, 32), (0, 5, 10, "Y")),
                ("encounter", {"kind": "thornback"}, (64, 64, 32, 32), (0, 2, 2, "F"))]
        sig = [(1, tool._signature("encounter", "", {"kind": "zealot"}), (320.0, 160.0)),
               (2, tool._signature("encounter", "", {"kind": "thornback"}), (64.0, 64.0))]
        gained = [("encounter", {"kind": "zealot", "when": "!phase:night"}, (320, 160, 32, 32), (0, 5, 10, "Y"))] + objs[1:]
        self.assertEqual(tool.assign_ids(gained, sig), [1, 2])
        changed = [("encounter", {"kind": "grave_robber"}, (320, 160, 32, 32), (0, 5, 10, "Y"))] + objs[1:]
        self.assertEqual(tool.assign_ids(changed, sig), [3, 2])                   # a different foe: a new id
        moved = [("encounter", {"kind": "zealot", "when": "!phase:night"}, (352, 160, 32, 32), (0, 5, 11, "Y"))] + objs[1:]
        self.assertEqual(tool.assign_ids(moved, sig), [3, 2])                     # moved AND changed: a new id (known)



class ViewTests(unittest.TestCase):
    """The map view: a rare foe's gold ring shows through the night; the people who come and go are drawn only while out."""

    @classmethod
    def setUpClass(cls):
        import pygame
        pygame.init()
        pygame.display.set_mode((64, 64))

    def render(self, ex, darkness=None, people=None):
        import pygame
        import gfx
        import rpg_map_view
        view = rpg_map_view.MapView(ex.area)
        view.darkness = ex.area.darkness_at(clock.now(ex.save.world)) if darkness is None else darkness
        view.flicker = False
        cam = explore.Camera(800, 600, 2)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        screen = pygame.Surface((800, 600))
        if people is None:
            people = [p for p in ex.present_people() if p["when"]]
        view.draw(screen, cam, ex.walker, ex.encounters(), 10.0, people=people)
        return screen, cam, gfx

    def test_a_rare_foes_ring_shows_through_the_night(self):
        with mock.patch.object(rare, "roll", lambda save, rid, minutes: 0.0):     # its window comes up
            ex = place(explorer_at(OUT, at(1, 22), ["warrens_entered"]), (51, 8))
            foe = ex.foes["ashfen_outskirts#rare:mother_of_rats"]
            self.assertTrue(ex.is_out(foe.spot))
            screen, cam, gfx = self.render(ex)
        x0, y0, x1, y1 = foe.box
        fx, fy = cam.to_screen(round((x0 + x1) / 2), round(y1))
        w = ex.area.tw * cam.zoom
        gold = sum(1 for x in range(int(fx - w * 0.6), int(fx + w * 0.6)) for y in range(int(fy - w * 0.3), int(fy + w * 0.3))
                   if 0 <= x < 800 and 0 <= y < 600 and tuple(screen.get_at((x, y)))[:3] == gfx.RARE_RING)
        self.assertGreater(gold, 20, "the ring is lost in the dark")
        with mock.patch.object(rare, "roll", lambda save, rid, minutes: 0.99):    # not out: no ring anywhere
            ex2 = place(explorer_at(OUT, at(1, 22), ["warrens_entered"]), (51, 8))
            screen2, _cam, _g = self.render(ex2)
        self.assertEqual(sum(1 for x in range(0, 800, 2) for y in range(0, 600, 2)
                             if tuple(screen2.get_at((x, y)))[:3] == gfx.RARE_RING), 0)

    def test_the_night_people_are_drawn_only_at_night(self):
        ex = place(explorer_at(TOWN, at(1, 22)), (21, 17))
        wenna = next(p for p in ex.present_people() if p["id"] == "mourner")
        night, cam, _g = self.render(ex, darkness=0.0)
        empty, _c, _g = self.render(ex, darkness=0.0, people=[])
        c, r = wenna["tile"]
        x, y = cam.to_screen(c * ex.area.tw, r * ex.area.th)
        w = ex.area.tw * cam.zoom
        differ = sum(1 for dx in range(w) for dy in range(w) if night.get_at((x + dx, y + dy)) != empty.get_at((x + dx, y + dy)))
        self.assertGreater(differ, w * w // 10, "Wenna isn't drawn on her tile")
        day = place(explorer_at(TOWN, at(1, 12)), (21, 17))
        self.assertNotIn("mourner", {p["id"] for p in day.present_people()})
