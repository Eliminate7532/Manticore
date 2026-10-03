# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 13: every game gets a seed and keeps every click (forge_client), bug reports carry them all, and replay.py plays a report again."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_table as ft
import replay
import reporting
from tests.test_forge_table import make_gui, move, point_for_card
from tests.test_round11 import mox, state_with


class SeedTests(unittest.TestCase):
    def test_new_seeds_are_positive_ints_that_fit_forges_long_and_differ(self):
        seeds = {fc.new_seed() for _ in range(50)}
        self.assertGreater(len(seeds), 40)
        self.assertTrue(all(isinstance(x, int) and 0 < x < 2 ** 31 for x in seeds))

    def start_command(self, seed):
        s = fc.ForgeSession("p.dck", ["o.dck"], name="Karl", seed=seed, runtime="rt")
        with mock.patch.object(fc, "runtime_problem", return_value=None), mock.patch.object(fc, "find_java", return_value="java"), \
                mock.patch.object(fc, "class_path", return_value="cp"), mock.patch.object(fc.subprocess, "Popen") as popen, \
                mock.patch.object(fc.threading, "Thread"):
            s.stderr_path = os.devnull
            s.start()
        return s, popen.call_args[0][0]

    def test_a_game_started_without_a_seed_picks_one_and_hands_it_to_forge(self):
        s, cmd = self.start_command(None)
        self.assertIsNotNone(s.seed)
        self.assertEqual(cmd[cmd.index("--seed") + 1], str(s.seed))

    def test_a_given_seed_is_kept(self):
        s, cmd = self.start_command(1234)
        self.assertEqual(s.seed, 1234)
        self.assertEqual(cmd[cmd.index("--seed") + 1], "1234")


class SentLogTests(unittest.TestCase):
    def test_every_click_is_kept_although_the_short_log_keeps_only_the_newest(self):
        s = fc.ForgeSession("p.dck", [], runtime="rt")
        s.proc = mock.Mock()
        s.proc.poll.return_value = None
        for i in range(500):
            s.send(c="card", id=i)
        self.assertEqual(len(s.sent_log), 400)
        self.assertEqual(len(s.sent_all), 500)
        self.assertEqual(s.sent_all[0][1], {"c": "card", "id": 0})
        self.assertEqual(s.sent_all[-1][1], {"c": "card", "id": 499})


class ReportCommandTests(unittest.TestCase):
    def build(self, n):
        tmp = tempfile.mkdtemp()
        folder = os.path.join(tmp, "game")
        os.makedirs(os.path.join(folder, "forge_decks"))
        for name in ("player.dck", "opponent1.dck"):
            with open(os.path.join(folder, "forge_decks", name), "w") as f:
                f.write("[metadata]\nName=X\n[Commander]\n1 Kinnan, Bonder Prodigy\n[Main]\n99 Forest\n")
        cmds = [(i * 0.5, {"c": "card", "id": i}) for i in range(n)]
        path = reporting.build_report({"name": "T", "happened": "h", "expected": "e", "seed": 77}, {"turn": 1}, ["[TURN] Turn 1 (T)"], cmds, None,
                                      folder=folder, dest_dir=tmp)
        return path

    def test_a_reports_holds_every_click_of_a_normal_game_and_says_it_is_complete(self):
        data = json.loads(zipfile.ZipFile(self.build(1200)).read("commands.json"))
        self.assertEqual(len(data["commands"]), 1200)
        self.assertTrue(data["complete"])
        self.assertEqual(data["seed"], 77)

    def test_a_huge_game_keeps_the_newest_clicks_and_says_it_is_not_complete(self):
        data = json.loads(zipfile.ZipFile(self.build(reporting.COMMAND_LINES + 50)).read("commands.json"))
        self.assertEqual(len(data["commands"]), reporting.COMMAND_LINES)
        self.assertFalse(data["complete"])
        self.assertEqual(data["commands"][-1]["id"], reporting.COMMAND_LINES + 49)

    def test_the_table_hands_the_whole_click_list_and_the_seed_to_the_report(self):
        gui = make_gui()
        gui.session.seed = 4242
        gui.session.sent_all = [(10.0 + i, {"c": "card", "id": i}) for i in range(300)]
        gui.session.sent_log = gui.session.sent_all[-40:]                    # the short tail the client keeps for its own use
        ctx = gui.report_context()
        self.assertEqual(ctx["seed"], 4242)
        self.assertEqual(len(ctx["commands"]), 300)
        self.assertEqual(ctx["commands"][0][0], 0)                          # times are relative to the first click
        self.assertEqual(ctx["commands"][-1][1]["id"], 299)


class LoadReportTests(unittest.TestCase):
    def zip(self, files):
        path = os.path.join(tempfile.mkdtemp(), "r.zip")
        with zipfile.ZipFile(path, "w") as z:
            for n, d in files.items():
                z.writestr(n, d if isinstance(d, str) else json.dumps(d))
        return path

    DECKS = {"decks/player.dck": "[Main]\n", "decks/opponent2.dck": "[Main]\n", "decks/opponent10.dck": "[Main]\n", "decks/check.dck": "x"}

    def files(self, **over):
        f = {"commands.json": {"seed": 5, "complete": True, "commands": [{"t": 0.0, "c": "ok"}, {"t": 1.2, "c": "reply", "id": 1, "value": [0]}]},
             "state.json": {"me": 0, "players": [{"id": 0, "name": "Karl"}, {"id": 1, "name": "AI 1"}]}, "game_log.txt": "[TURN] x\n"}
        f.update(self.DECKS)
        f.update(over)
        return f

    def test_a_good_report_is_read(self):
        r = replay.load_report(self.zip(self.files()))
        self.assertEqual((r.seed, r.name, r.complete), (5, "Karl", True))
        self.assertEqual(r.commands, [{"c": "ok"}, {"c": "reply", "id": 1, "value": [0]}])          # the timestamps are dropped
        self.assertEqual(sorted(r.decks), ["check.dck", "opponent10.dck", "opponent2.dck", "player.dck"])

    def test_a_report_without_a_seed_is_refused_in_plain_words(self):
        f = self.files()
        f["commands.json"] = {"seed": None, "commands": []}
        with self.assertRaises(replay.ReportError) as cm:
            replay.load_report(self.zip(f))
        self.assertIn("Seed: None", str(cm.exception))

    def test_missing_pieces_are_refused(self):
        f = self.files()
        del f["commands.json"]
        with self.assertRaises(replay.ReportError):
            replay.load_report(self.zip(f))
        with self.assertRaises(replay.ReportError):
            replay.load_report(self.zip({k: v for k, v in self.files().items() if not k.startswith("decks/opponent")}))
        with self.assertRaises(replay.ReportError):
            replay.load_report(os.path.join(tempfile.mkdtemp(), "nothing.zip"))

    def test_old_reports_without_a_complete_flag_count_as_incomplete(self):
        f = self.files()
        f["commands.json"] = {"seed": 5, "commands": []}
        self.assertFalse(replay.load_report(self.zip(f)).complete)


def board(turn=3, life=(40, 38), hand=("Forest",)):
    return {"turn": turn, "phase": "MAIN1", "prompt": {"message": "Priority: Karl\nTurn: 3"},
            "players": [{"id": 0, "name": "Karl", "life": life[0], "zones": {"battlefield": [{"name": "Sol Ring"}], "hand": [{"name": n} for n in hand], "graveyard": []}},
                        {"id": 1, "name": "AI", "life": life[1], "zones": {"battlefield": [], "hand": [], "graveyard": []}}]}


class CompareTests(unittest.TestCase):
    def test_equal_boards_have_no_differences(self):
        self.assertEqual(replay.differences(replay.summary(board()), replay.summary(board())), [])

    def test_differences_name_what_changed(self):
        d = replay.differences(replay.summary(board()), replay.summary(board(turn=4, life=(37, 38), hand=("Forest", "Island"))))
        text = " | ".join(d)
        self.assertIn("turn", text)
        self.assertIn("Karl: life 40 in the report, 37 in the replay", text)
        self.assertIn("Karl: hand differs", text)

    def test_the_card_order_inside_a_zone_does_not_matter(self):
        a, b = board(hand=("Forest", "Island")), board(hand=("Island", "Forest"))
        self.assertEqual(replay.differences(replay.summary(a), replay.summary(b)), [])


class FakeSession:
    """Just enough of ForgeSession for the Replayer: it 'settles' at once and can be told to ask questions."""
    def __init__(self, asks=(), ok_enabled=True):
        self.state = {"prompt": {"ok": {"enabled": ok_enabled}, "cancel": {"enabled": True}}}
        self.state_version = 1
        self.ready, self.exited, self.fatal = True, False, None
        self.requests = list(asks)
        self.sent = []

    def poll(self):
        pass

    def send(self, **cmd):
        self.sent.append(cmd)
        return True

    def answer(self, request, value):
        self.requests.remove(request)
        self.sent.append({"c": "reply", "id": request["id"], "value": value})


class ReplayerTests(unittest.TestCase):
    def run_it(self, session, commands, **kw):
        r = replay.Replayer(session, commands, settle=0.01, wait_limit=0.3, **kw)
        return r, r.run()

    def test_clicks_are_sent_in_order(self):
        s = FakeSession()
        cmds = [{"c": "ok"}, {"c": "card", "id": 3}, {"c": "cancel"}]
        r, ok = self.run_it(s, cmds)
        self.assertTrue(ok)
        self.assertEqual(s.sent, cmds)
        self.assertEqual(r.sent, 3)

    def test_an_answer_waits_for_its_question_and_is_sent_with_the_recorded_value(self):
        s = FakeSession(asks=[{"id": 4, "kind": "choose"}])
        r, ok = self.run_it(s, [{"c": "reply", "id": 4, "value": [2]}])
        self.assertTrue(ok)
        self.assertEqual(s.sent, [{"c": "reply", "id": 4, "value": [2]}])

    def test_a_question_that_never_comes_means_the_replay_diverged(self):
        r, ok = self.run_it(FakeSession(), [{"c": "ok"}, {"c": "reply", "id": 9, "value": True}])
        self.assertFalse(ok)
        self.assertEqual(r.diverged[0], 1)
        self.assertIn("question 9", r.diverged[1])

    def test_a_button_that_never_enables_means_the_replay_diverged(self):
        r, ok = self.run_it(FakeSession(ok_enabled=False), [{"c": "ok"}])
        self.assertFalse(ok)
        self.assertIn("ok button", r.diverged[1])

    def test_upto_stops_early_and_quit_ends_the_replay(self):
        s = FakeSession()
        r = replay.Replayer(s, [{"c": "ok"}, {"c": "ok"}, {"c": "ok"}], settle=0.01, wait_limit=0.3)
        self.assertTrue(r.run(upto=2))
        self.assertEqual(len(s.sent), 2)
        s2 = FakeSession()
        r2 = replay.Replayer(s2, [{"c": "ok"}, {"c": "quit"}, {"c": "ok"}], settle=0.01, wait_limit=0.3)
        self.assertTrue(r2.run())
        self.assertEqual(s2.sent, [{"c": "ok"}])

    def test_an_engine_that_never_starts_is_reported(self):
        s = FakeSession()
        s.ready = False
        r = replay.Replayer(s, [{"c": "ok"}], settle=0.01, wait_limit=0.2, start_limit=0.2)
        self.assertFalse(r.run())
        self.assertIn("never started", r.diverged[1])


class CommandLineTests(unittest.TestCase):
    def test_a_report_without_a_seed_is_explained_and_exits_with_2(self):
        path = os.path.join(tempfile.mkdtemp(), "r.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("commands.json", json.dumps({"seed": None, "commands": []}))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = replay.main([path])
        self.assertEqual(code, 2)
        self.assertIn("Seed: None", out.getvalue())


def creature(keywords=None, text="", cid=70, **extra):
    """A vanilla 1/1 on my battlefield (Merfolk of the Pearl Trident) with the keywords Forge reports for it right now."""
    card = dict(mox(), id=cid, name="Merfolk of the Pearl Trident", oracleName="Merfolk of the Pearl Trident", type="Creature - Merfolk", cost="{U}",
                colors=["U"], text=text, isCreature=True, power=1, toughness=1)
    if keywords is not None:
        card["keywords"] = keywords
    card.update(extra)
    return card


class GainedKeywordTests(unittest.TestCase):
    def test_a_keyword_the_text_does_not_mention_is_gained(self):
        self.assertEqual(ft.gained_keywords(creature(["Flying"])), ["Flying"])
        self.assertEqual(ft.gained_keywords(creature(["Flying", "Haste"], "Haste")), ["Flying"])

    def test_printed_keywords_are_not_flagged(self):
        self.assertEqual(ft.gained_keywords(creature(["Flying", "Deathtouch"], "Flying, deathtouch\nWhen this enters, draw a card.")), [])
        self.assertEqual(ft.gained_keywords(creature(["Ward {2}"], "Ward\u2014{2}")), [])

    def test_first_strike_is_not_mistaken_for_a_first_main_phase(self):
        text = "At the beginning of your first main phase, add {G}."
        self.assertEqual(ft.gained_keywords(creature(["First strike"], text)), ["First strike"])
        self.assertEqual(ft.gained_keywords(creature(["First strike"], "First strike")), [])

    def test_nothing_reported_means_nothing_gained(self):
        self.assertEqual(ft.gained_keywords(creature()), [])
        self.assertEqual(ft.gained_keywords(creature([])), [])
        self.assertEqual(ft.gained_keywords({"text": None, "keywords": None}), [])

    def test_each_keyword_is_listed_once(self):
        self.assertEqual(ft.gained_keywords(creature(["Flying", "Flying"])), ["Flying"])


class KeywordDrawingTests(unittest.TestCase):
    def pixels(self, card, w=120, h=168):
        gui = make_gui(state_with(card))
        return __import__("pygame").image.tostring(gui.card_surface(card, w, h, True), "RGB")

    def test_a_gained_keyword_puts_a_chip_on_the_card(self):
        plain, flying = self.pixels(creature([])), self.pixels(creature(["Flying"]))
        self.assertNotEqual(plain, flying)

    def test_a_printed_keyword_adds_nothing(self):
        self.assertEqual(self.pixels(creature(["Flying"], "Flying")), self.pixels(creature([], "Flying")))

    def test_more_than_two_gained_keywords_add_a_more_chip(self):
        two, three = self.pixels(creature(["Flying", "Haste"])), self.pixels(creature(["Flying", "Haste", "Trample"]))
        self.assertNotEqual(two, three)

    def test_the_chip_is_violet(self):
        gui = make_gui(state_with(creature(["Flying"])))
        surf = gui.card_surface(creature(["Flying"]), 200, 280, True)
        found = any(surf.get_at((x, y))[:3] == (74, 52, 130) for x in range(0, 100) for y in range(150, 280))
        self.assertTrue(found, "no violet chip in the lower left of the card")

    def test_a_gained_keyword_and_an_imprint_pill_fit_on_the_same_card(self):
        both = dict(mox([{"name": "Samut", "colors": ["U"]}]), keywords=["Hexproof"])
        self.assertNotEqual(self.pixels(both), self.pixels(mox([{"name": "Samut", "colors": ["U"]}])))


class KeywordCaptionTests(unittest.TestCase):
    def gui(self, card):
        return make_gui(state_with(card))

    def test_the_caption_lists_gained_keywords(self):
        card = creature(["Flying", "Haste"])
        self.assertEqual(self.gui(card).keyword_caption(card), "Gained: Flying, Haste")

    def test_no_caption_without_gains_or_for_hidden_cards(self):
        gui = self.gui(creature([]))
        self.assertEqual(gui.keyword_caption(creature(["Flying"], "Flying")), "")
        self.assertEqual(gui.keyword_caption(dict(creature(["Flying"]), hidden=True)), "")
        self.assertEqual(gui.keyword_caption(creature()), "")

    def test_hovering_a_creature_with_a_gained_keyword_draws_the_strip_and_it_stays_inside_the_picture(self):
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.5)):
            card = creature(["Flying", "Haste", "Trample", "Menace", "Hexproof from blue"])
            gui = make_gui(state_with(card), size=size, scale=scale)
            move(gui, point_for_card(gui, 70))
            strip = gui.draw_imprint_caption(card, gui.L.preview)
            self.assertIsNotNone(strip, (size, scale))
            self.assertTrue(gui.L.preview.contains(strip), (size, scale, strip, gui.L.preview))

    def test_imprint_and_keyword_notes_share_one_strip(self):
        card = dict(mox([{"name": "Samut, Tyrant of Naktamun", "colors": ["U"]}]), keywords=["Hexproof"])
        gui = self.gui(card)
        one = gui.draw_imprint_caption(mox([{"name": "Samut, Tyrant of Naktamun", "colors": ["U"]}]), gui.L.preview)
        both = gui.draw_imprint_caption(card, gui.L.preview)
        self.assertGreater(both.h, one.h)


if __name__ == "__main__":
    unittest.main()
