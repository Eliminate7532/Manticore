# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 44 (Karl's decisions 6-9 of 6 Oct 2026): stats - your record and matchups, and when and how your games end.

  RecorderTests   stats.Recorder over made-up snapshots and events: the start line; combat damage, noncombat damage, a drain
                  (the card that resolved with it), life paid as a cost (the spell cast next), commander damage, poison, an empty
                  library, a "lose the game" card, an opponent's alternate win, my alternate win, a win by the last opponent out,
                  a draw; without Forge's result the board decides; seat and my turns; concede and close; reading it back
  SummaryTests    stats.summarize / decks / end_lines: only games played to the end count, conceded and unfinished apart, by
                  pod size, seat and opponent's deck, online a separate record, the game on the table left out, the words
  TableTests      the table records a game from its first board to Forge's result, waits for that result, closes a game
                  that goes away, marks a concede, keeps none for someone watching; the end-of-game screen's lines; the deck
                  screen's Start gives the decks' ids; the journal keeps the game's id for Resume
  PageTests       the deck screen's Stats button, the page (This deck / All decks) at several window sizes
  SourceTests     Outcome.java, the result in game_over, the jar; the stats folder (tests never touch yours; never public)
  LiveTests       real Forge: a Lightning Bolt win, a loss to Kambal's drain, a Thassa's Oracle win, a concede
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

pygame.init()

import forge_client as fc
import forge_table as ft
import journal as gjournal
import paths
import stats
import stats_view
import tests.live as live
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import click, frame, key

BRIDGE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


# ---- made-up games ------------------------------------------------------------------------------------------------------------

def card(cid, name, ctrl):
    return {"id": cid, "name": name, "controller": ctrl}


def player(pid, name, cmdr_id, cmdr, ai=False, life=40, lost=False, battlefield=(), **kw):
    p = {"id": pid, "name": name, "ai": ai, "life": life, "lost": lost, "commanders": [cmdr_id], "libraryCount": 60,
         "zones": {"command": [card(cmdr_id, cmdr, pid)], "battlefield": [card(c, n, pid) for c, n in battlefield],
                   "graveyard": [], "exile": []}, "commanderDamage": {}}
    p.update(kw)
    return p


def state(players, turn=1, active=0, over=False, winner=None, stack=()):
    st = {"t": "state", "turn": turn, "activePlayer": active, "me": 0, "players": players, "stack": list(stack),
          "gameOver": over}
    if winner:
        st["winner"] = winner
    return st


def ev(kind, **kw):
    return dict({"t": "event", "kind": kind}, **kw)


def two(**me_kw):
    return [player(0, "Karl", 100, "Veyran, Voice of Duality", **me_kw),
            player(1, "AI 1 (Kinnan)", 200, "Kinnan, Bonder Prodigy", ai=True,
                   battlefield=[(201, "Grizzly Bears"), (202, "Kambal, Consul of Allocation")])]


def row(pid, name, seat, won, turns=5, **kw):
    return dict({"id": pid, "name": name, "seat": seat, "won": won, "turns": turns, "mulligans": 0}, **kw)


def outcome(rows, **kw):
    return dict({"endReason": "AllOpponentsLost", "lastTurn": 9, "draw": False, "players": rows}, **kw)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stats_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def rec(self, **info):
        return stats.Recorder(dict({"id": "g1", "deck": {"id": "my_decks/veyran.txt", "name": "Veyran"},
                                    "opponent_decks": [{"id": "sample:kinnan", "name": "Kinnan NBC"}], "folder": self.tmp}, **info))

    def lines(self):
        with open(stats.stats_path(self.tmp), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


class RecorderTests(Tmp):
    def test_the_start_line_names_decks_commanders_and_players(self):
        r = self.rec(format="commander")
        r.note_state(state(two()))
        start = self.lines()[0]
        self.assertEqual(start["t"], "start")
        self.assertEqual(start["players"], 2)
        self.assertEqual(start["deck"], {"id": "my_decks/veyran.txt", "name": "Veyran", "commanders": ["Veyran, Voice of Duality"]})
        self.assertEqual(start["opponents"], [{"name": "AI 1 (Kinnan)", "ai": True, "deck": {"id": "sample:kinnan", "name": "Kinnan NBC"},
                                               "commanders": ["Kinnan, Bonder Prodigy"]}])
        r.note_state(state(two(), turn=2))
        self.assertEqual(len(self.lines()), 1, "one start line a game")

    def test_nothing_is_written_before_a_board_is_seen(self):
        r = self.rec()
        self.assertIsNone(r.finish(None, None))
        self.assertIsNone(r.close())
        self.assertFalse(os.path.exists(stats.stats_path(self.tmp)))

    def lose(self, events, me_kw=None, my_row=None, ai_row=None, final_kw=None):
        r = self.rec()
        r.note_state(state(two()))
        for e in events:
            r.note_event(e)
        mine = dict({"life": 0, "lost": True}, **(me_kw or {}))
        final = state(two(**mine), turn=9, active=1, over=True, winner="AI 1 (Kinnan)", **(final_kw or {}))
        res = outcome([my_row or row(0, "Karl", 2, False, turns=4, loss="LifeReachedZero"),
                       ai_row or row(1, "AI 1 (Kinnan)", 1, True)])
        return r.finish(res, final)

    def test_combat_damage_names_the_creature_and_its_controller(self):
        end = self.lose([ev("turn", player=1, turn=1), ev("turn", player=0, turn=2),
                         ev("damage_player", player=0, source=201, amount=3, combat=True), ev("life", player=0, old=3, new=0)])
        self.assertEqual(end["result"], "lost")
        self.assertEqual(end["how"], {"cause": "combat", "card": "Grizzly Bears", "by": "AI 1 (Kinnan)"})
        self.assertEqual((end["seat"], end["my_turns"], end["turn"], end["source"]), (2, 4, 9, "forge"))
        self.assertEqual(self.lines()[-1], end)

    def test_noncombat_damage(self):
        end = self.lose([ev("damage_player", player=0, source=202, amount=5, combat=False), ev("life", player=0, old=5, new=0)])
        self.assertEqual(end["how"]["cause"], "damage")
        self.assertEqual(end["how"]["card"], "Kambal, Consul of Allocation")

    def test_a_drain_is_the_card_that_resolved_with_it(self):
        end = self.lose([ev("cast", card=300, player=0), ev("cast", card=202, player=1, trigger=True),
                         ev("life", player=0, old=2, new=0), ev("resolve", card=202)])
        self.assertEqual(end["how"], {"cause": "life_loss", "card": "Kambal, Consul of Allocation", "by": "AI 1 (Kinnan)"})

    def test_life_paid_as_a_cost_is_the_spell_cast_next(self):
        r = self.rec()
        r.note_state(state(two(), stack=[{"card": card(101, "Toxic Deluge", 0)}]))
        r.note_event(ev("life", player=0, old=3, new=0))
        r.note_event(ev("cast", card=101, player=0))
        end = r.finish(outcome([row(0, "Karl", 1, False, loss="LifeReachedZero"), row(1, "AI 1 (Kinnan)", 2, True)]),
                       state(two(life=0, lost=True), over=True, winner="AI 1 (Kinnan)"))
        self.assertEqual(end["how"], {"cause": "life_loss", "card": "Toxic Deluge", "by": "Karl", "mine": True})
        self.assertIn("from your Toxic Deluge", stats.end_lines(end)[0])

    def test_damage_then_its_life_change_is_not_also_a_drain(self):
        end = self.lose([ev("damage_player", player=0, source=201, amount=2, combat=True), ev("life", player=0, old=2, new=0),
                         ev("resolve", card=202)])
        self.assertEqual(end["how"]["card"], "Grizzly Bears")

    def test_commander_damage_names_the_commander(self):
        end = self.lose([], me_kw={"life": 19, "commanderDamage": {"200": 21}},
                        my_row=row(0, "Karl", 2, False, loss="CommanderDamage"))
        self.assertEqual(end["how"], {"cause": "commander", "card": "Kinnan, Bonder Prodigy", "by": "AI 1 (Kinnan)"})

    def test_poison_an_empty_library_and_a_lose_the_game_card(self):
        for loss, extra, cause, card_name in (("Poisoned", {}, "poison", None), ("Milled", {}, "milled", None),
                                               ("SpellEffect", {"lossSpell": "Phage the Untouchable"}, "lose_effect",
                                                "Phage the Untouchable")):
            with self.subTest(loss=loss):
                end = self.lose([], me_kw={"life": 30}, my_row=row(0, "Karl", 2, False, loss=loss, **extra))
                self.assertEqual(end["how"]["cause"], cause)
                self.assertEqual(end["how"]["card"], card_name)

    def test_an_opponents_alternate_win(self):
        end = self.lose([], me_kw={"life": 30}, my_row=row(0, "Karl", 2, False, loss="OpponentWon"),
                        ai_row=row(1, "AI 1 (Kinnan)", 1, True, altWin="Thassa's Oracle"))
        self.assertEqual(end["how"], {"cause": "alt_win", "card": "Thassa's Oracle", "by": "AI 1 (Kinnan)"})
        self.assertEqual(stats.end_lines(end)[0],
                         "Lost on your turn 5 (game turn 9) - an alternate win with AI 1 (Kinnan)'s Thassa's Oracle.")

    def test_my_alternate_win(self):
        r = self.rec()
        r.note_state(state(two()))
        end = r.finish(outcome([row(0, "Karl", 1, True, turns=6, altWin="Thassa's Oracle"),
                                row(1, "AI 1 (Kinnan)", 2, False, loss="OpponentWon")], endReason="WinsGameSpellEffect"),
                       state(two(), over=True, winner="Karl"))
        self.assertEqual(end["result"], "won")
        self.assertEqual(end["how"], {"cause": "alt_win", "card": "Thassa's Oracle", "by": "Karl", "mine": True})
        self.assertEqual(stats.end_lines(end)[0], "Won on your turn 6 (game turn 9) - an alternate win with your Thassa's Oracle.")

    def test_a_win_is_how_the_last_opponent_went_out(self):
        three = two() + [player(2, "AI 2 (Teysa)", 300, "Teysa Karlov", ai=True, battlefield=[(301, "Blood Artist")])]
        r = self.rec()
        r.note_state(state(three))
        r.note_state(state([three[0], dict(three[1], life=0, lost=True), three[2]], turn=5))      # AI 1 out on turn 5
        r.note_event(ev("damage_player", player=2, source=102, amount=3, combat=False))
        r.note_state(state([three[0], dict(three[1], life=0, lost=True), three[2]], turn=8,
                           stack=[{"card": card(102, "Lightning Bolt", 0)}]))
        r.note_event(ev("life", player=2, old=3, new=0))
        final = state([three[0], dict(three[1], life=0, lost=True), dict(three[2], life=0, lost=True)], turn=8, over=True, winner="Karl")
        end = r.finish(outcome([row(0, "Karl", 3, True, turns=3), row(1, "AI 1 (Kinnan)", 1, False, loss="LifeReachedZero"),
                                row(2, "AI 2 (Teysa)", 2, False, loss="LifeReachedZero")]), final)
        self.assertEqual(end["result"], "won")
        self.assertEqual(end["how"], {"cause": "damage", "card": "Lightning Bolt", "by": "Karl", "mine": True})
        self.assertEqual(end["seat"], 3)
        self.assertEqual(sorted(e["name"] for e in end["eliminations"]), ["AI 1 (Kinnan)", "AI 2 (Teysa)"])
        self.assertEqual(stats.end_lines(end)[0], "Won on your turn 3 (game turn 9) - noncombat damage from your Lightning Bolt.")

    def test_a_draw(self):
        r = self.rec()
        r.note_state(state(two()))
        end = r.finish(outcome([row(0, "Karl", 1, False, loss="IntentionalDraw"), row(1, "AI 1 (Kinnan)", 2, False,
                                                                                         loss="IntentionalDraw")], draw=True),
                       state(two(), over=True))
        self.assertEqual((end["result"], end["how"]), ("draw", {"cause": "draw"}))

    def test_without_forges_result_the_board_decides(self):
        r = self.rec()
        r.note_state(state(two()))
        r.note_event(ev("damage_player", player=0, source=201, amount=2, combat=True))
        r.note_event(ev("life", player=0, old=2, new=0))
        end = r.finish(None, state(two(life=0, lost=True), over=True, winner="AI 1 (Kinnan)"))
        self.assertEqual((end["result"], end["how"]["cause"], end["how"]["card"], end["source"]),
                         ("lost", "combat", "Grizzly Bears", "table"))
        r2 = self.rec(id="g2")
        r2.note_state(state(two()))
        self.assertEqual(r2.finish(None, state(two(), over=True, winner="Karl"))["result"], "won")
        r3 = self.rec(id="g3")
        r3.note_state(state(two(commanderDamage={"200": 22}, life=18)))
        end3 = r3.finish(None, state(two(commanderDamage={"200": 22}, life=18, lost=True), over=True, winner="AI 1 (Kinnan)"))
        self.assertEqual((end3["how"]["cause"], end3["how"]["card"]), ("commander", "Kinnan, Bonder Prodigy"))

    def test_an_empty_library_is_milled_only_when_it_ran_out_in_play(self):
        # A player who loses a pod leaves with all their cards: 60 cards to 0 at once is not a mill; 3 to 0 is.
        for before, cause in ((60, "unknown"), (3, "milled")):
            with self.subTest(before=before):
                r = self.rec(id=f"m{before}")
                r.note_state(state(two(libraryCount=before)))
                end = r.finish(None, state(two(libraryCount=0, lost=True, life=30), over=True, winner="AI 1 (Kinnan)"))
                self.assertEqual(end["how"]["cause"], cause)

    def test_seat_and_my_turns_without_forges_result(self):
        r = self.rec()
        r.note_state(state(two(), turn=1, active=1))
        for t, p in ((1, 1), (2, 0), (3, 1), (4, 0), (5, 1)):
            r.note_event(ev("turn", player=p, turn=t))
        end = r.finish(None, state(two(life=0, lost=True), turn=5, active=1, over=True, winner="AI 1 (Kinnan)"))
        self.assertEqual((end["seat"], end["my_turns"], end["turn"]), (2, 2, 5))

    def test_concede_and_close(self):
        r = self.rec()
        r.note_state(state(two()))
        r.conceded = True
        self.assertEqual(r.close()["result"], "conceded")
        r2 = self.rec(id="g2")
        r2.note_state(state(two()))
        self.assertEqual(r2.close()["result"], "unfinished")
        r3 = self.rec(id="g3")
        r3.note_state(state(two(life=0, lost=True)))           # out of a pod, then left before it ended
        self.assertEqual(r3.close()["result"], "lost")
        r4 = self.rec(id="g4")
        r4.note_state(state(two()))
        r4.conceded = True                                     # conceded, then Forge finished the game
        self.assertEqual(r4.finish(outcome([row(0, "Karl", 1, False, loss="Conceded"), row(1, "AI 1 (Kinnan)", 2, True)]),
                                   state(two(lost=True), over=True, winner="AI 1 (Kinnan)"))["result"], "conceded")

    def test_out_of_a_pod_says_how(self):
        r = self.rec()
        r.note_state(state(two()))
        r.note_event(ev("turn", player=0, turn=2))
        r.note_event(ev("damage_player", player=0, source=201, amount=4, combat=True))
        r.note_event(ev("life", player=0, old=4, new=0))
        r.note_state(state(two(life=0, lost=True), turn=3, active=1))
        self.assertEqual(r.out_line(), "Out on your turn 2 - combat damage from AI 1 (Kinnan)'s Grizzly Bears.")

    def test_reading_back_a_resumed_game_and_a_torn_line(self):
        r = self.rec()
        r.note_state(state(two()))
        r.close()                                              # the program closed mid-game
        again = self.rec(resumed=True)                         # Resume: the same id
        again.note_state(state(two()))
        again.finish(outcome([row(0, "Karl", 1, True), row(1, "AI 1 (Kinnan)", 2, False, loss="LifeReachedZero")]),
                     state(two(), over=True, winner="Karl"))
        with open(stats.stats_path(self.tmp), "a", encoding="utf-8") as f:
            f.write('{"t": "end", "id": "g1", "res')           # a crash mid-write
        games = stats.load(self.tmp)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["end"]["result"], "won")
        self.assertNotIn("resumed", games[0]["start"])         # the first start line is the game's


class SummaryTests(Tmp):
    def game(self, gid, result, deck="d1", name="Veyran", players=4, seat=1, opp=("Kinnan NBC",), my_turns=6, how=None,
             online=None):
        stats.append({"t": "start", "v": 1, "id": gid, "at": 1.0, "format": "commander", "online": online, "players": players,
                      "deck": {"id": deck, "name": name, "commanders": ["Veyran"]},
                      "opponents": [{"name": "AI", "ai": True, "deck": {"id": None, "name": o}, "commanders": ["X"]} for o in opp]},
                     self.tmp)
        if result:
            stats.append({"t": "end", "v": 1, "id": gid, "at": 2.0, "result": result, "seat": seat, "my_turns": my_turns,
                          "turn": 20, "how": how or {}, "eliminations": []}, self.tmp)

    def test_only_games_played_to_the_end_count(self):
        self.game("a", "won", how={"cause": "combat", "card": "Craterhoof Behemoth", "by": "Karl", "mine": True})
        self.game("b", "lost", how={"cause": "alt_win", "card": "Thassa's Oracle", "by": "AI 2"}, seat=3, opp=("Kinnan NBC", "Teysa"))
        self.game("c", "lost", players=2, how={"cause": "combat", "card": "Grizzly Bears", "by": "AI 1"}, my_turns=4)
        self.game("d", "conceded")
        self.game("e", None)                                   # unfinished (a crash)
        self.game("f", None)                                   # the game on the table
        s = stats.summarize(stats.load(self.tmp), "d1", current_id="f")
        self.assertEqual((s["won"], s["lost"], s["draw"], s["conceded"], s["unfinished"], s["games"]), (1, 2, 0, 1, 1, 5))
        self.assertEqual(dict(s["pods"]), {4: [1, 1, 0], 2: [0, 1, 0]})
        self.assertEqual(dict(s["seats"]), {1: [1, 1, 0], 3: [0, 1, 0]})
        self.assertEqual(dict(s["opponents"]), {"Kinnan NBC": [1, 2, 0], "Teysa": [0, 1, 0]})
        self.assertEqual((s["win_turns"], sorted(s["loss_turns"])), ([6], [4, 6]))
        self.assertEqual(s["lost_to"], {"alt_win": 1, "combat": 1})
        self.assertEqual(s["killers"][("Thassa's Oracle", "AI 2")], 1)
        self.assertEqual(stats.record_words(s), "1 won, 2 lost (33%)")

    def test_online_is_a_separate_record_and_decks_are_apart(self):
        self.game("a", "won")
        self.game("b", "lost", online="guest")
        self.game("c", "won", deck="d2", name="Lathril")
        self.game("d", "won", deck="d2", name="Lathril")
        self.game("e", "lost", deck="d2", name="Lathril")
        games = stats.load(self.tmp)
        self.assertEqual(stats.summarize(games, "d1")["won"], 1)
        self.assertEqual(stats.summarize(games, "d1", online=True)["lost"], 1)
        rows = stats.decks(games)
        self.assertEqual([r[1] for r in rows], ["Lathril", "Veyran"])
        self.assertEqual(rows[1][3]["lost"], 1)

    def test_the_words(self):
        self.assertEqual(stats.turn_words([5, 7, 9]), "average 7.0, fastest 5, slowest 9")
        self.assertEqual(stats.turn_words([]), "-")
        end = {"result": "lost", "my_turns": 5, "turn": 21, "how": {"cause": "commander", "card": "Light-Paws, Emperor's Voice",
                                                                   "by": "AI 1 (opp1)"}}
        s = {"won": 3, "lost": 4, "draw": 0}
        self.assertEqual(stats.end_lines(end, s, "Veyran"),
                         ["Lost on your turn 5 (game turn 21) - commander damage from AI 1 (opp1)'s Light-Paws, Emperor's Voice.",
                          "Veyran: 3 won, 4 lost (43%)."])
        self.assertEqual(stats.end_lines({"result": "conceded", "my_turns": 2}), ["Conceded on your turn 2 - not counted in your record."])
        self.assertEqual(stats.end_lines(dict(end, how={"cause": "milled"})), [
            "Lost on your turn 5 (game turn 21) - an empty library."])


# ---- the table ---------------------------------------------------------------------------------------------------------------

class TableTests(Tmp):
    def setUp(self):
        super().setUp()
        p = mock.patch("stats.folder", return_value=self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def table(self, info=None, state_name="main1_start", spectator=False):
        session = FakeSession(load_state(state_name), load_log())
        session.stats_info = info if info is not None else {"id": "t1", "format": "commander", "online": None,
                                                             "deck": {"id": "my_decks/kinnan.txt", "name": "Kinnan"},
                                                             "opponent_decks": [{"id": "sample:x", "name": "Kinnan NBC"}]}
        session.spectator = spectator
        gui = ft.ForgeTable(session, StubStore(), window_size=(1360, 840))
        frame(gui, 2)
        return gui, session

    def over(self, session, result=True):
        st = load_state("combat_damage")
        session.handle(st)
        if result:
            session.handle({"t": "game_over", "result": outcome([row(0, "Karl", 2, False, turns=8, loss="LifeReachedZero"),
                                                                 row(1, "AI 1 (Kinnan)", 1, True)], lastTurn=16)})

    def test_a_game_is_recorded_from_its_first_board_to_forges_result(self):
        gui, session = self.table()
        self.assertEqual([x["t"] for x in self.lines()], ["start"])
        self.assertEqual(self.lines()[0]["deck"]["id"], "my_decks/kinnan.txt")
        self.over(session)
        frame(gui, 2)
        end = self.lines()[-1]
        self.assertEqual((end["t"], end["result"], end["seat"], end["my_turns"], end["turn"], end["source"]),
                         ("end", "lost", 2, 8, 16, "forge"))
        self.assertIsNotNone(gui.end_screen)
        lines = gui.end_stats_lines()
        self.assertTrue(lines[0].startswith("Lost on your turn 8 (game turn 16)"), lines)
        self.assertEqual(lines[1], "Kinnan: 0 won, 1 lost (0%).")
        drawn = []
        real = ft.flow.draw_text if hasattr(ft.flow, "draw_text") else None
        with mock.patch("flow_screens.draw_text", side_effect=lambda scr, text, *a, **k: (drawn.append(text), real(scr, text, *a, **k))[1]):
            gui.render()
        self.assertIn(lines[1], drawn, "the end-of-game screen shows the record")

    def test_without_forges_result_the_table_waits_then_decides(self):
        gui, session = self.table()
        self.over(session, result=False)
        frame(gui, 2)
        self.assertEqual(self.lines()[-1]["t"], "start", "it waits for the result that follows the snapshot")
        with mock.patch.object(ft, "STATS_RESULT_WAIT", 0.0):
            time.sleep(0.01)
            frame(gui, 2)
        end = self.lines()[-1]
        self.assertEqual((end["result"], end["source"]), ("lost", "table"))

    def test_a_new_session_closes_the_old_game(self):
        gui, session = self.table()
        gui.session = FakeSession(load_state("main1_start"))
        frame(gui, 2)
        ends = [x for x in self.lines() if x["t"] == "end"]
        self.assertEqual([(e["id"], e["result"]) for e in ends], [("t1", "unfinished")])
        self.assertNotEqual(gui.stats_rec.id, "t1")

    def test_concede_is_not_counted(self):
        gui, session = self.table()
        gui.concede()
        self.over(session)
        frame(gui, 2)
        self.assertEqual(self.lines()[-1]["result"], "conceded")
        self.assertEqual(gui.end_stats_lines()[0], "Conceded on your turn 8 - not counted in your record.")

    def test_closing_the_program_writes_an_unfinished_game(self):
        gui, session = self.table()
        gui.stats_close()
        self.assertEqual(self.lines()[-1]["result"], "unfinished")

    def test_someone_watching_keeps_no_record(self):
        gui, session = self.table(spectator=True)
        self.over(session)
        frame(gui, 2)
        self.assertIsNone(gui.stats_rec)
        self.assertFalse(os.path.exists(stats.stats_path(self.tmp)))
        self.assertEqual(gui.end_stats_lines(), [])

    def test_the_journal_keeps_the_games_stats_identity(self):
        j = gjournal.GameJournal(self.tmp)
        info = {"id": "abc", "deck": {"id": "d", "name": "Veyran"}}
        j.start(1, "Karl", {"player.dck": "x"}, "code", 1.0, stats=info)
        j.command({"c": "ok"}, 2.0)
        start, _cmds = gjournal.unfinished(self.tmp)
        self.assertEqual(start["stats"], info)

    def test_the_local_identity_comes_from_the_deck_screen(self):
        gui, _s = self.table()
        gui.stats_decks = ({"id": "my_decks/a.txt", "name": "A"}, [{"id": "sample:b", "name": "B"}])
        info = gui.local_stats_info(None, "brawl")
        self.assertEqual((info["deck"]["id"], info["opponent_decks"][0]["name"], info["format"]), ("my_decks/a.txt", "B", "brawl"))
        self.assertNotEqual(info["id"], gui.local_stats_info(None, None)["id"], "every game has its own id")


class StartTests(TempDecks):
    def test_start_gives_the_session_the_decks_ids(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch("stats.folder", return_value=tmp):
            gui = self.idle_gui()
            click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
            click(gui, self.button_point(gui, "start"))
            info = gui.session.stats_info
            self.assertEqual(info["deck"]["name"], "Kinnan NBC (sample)")
            self.assertTrue(info["deck"]["id"])
            self.assertEqual(len(info["opponent_decks"]), 1)
            gui.vs.skipped = True
            frame(gui, 3)
            self.assertEqual(gui.stats_rec.id, info["id"])


class PageTests(TempDecks):
    def setUp(self):
        super().setUp()
        self.stats_tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.stats_tmp, True)
        p = mock.patch("stats.folder", return_value=self.stats_tmp)
        p.start()
        self.addCleanup(p.stop)

    def record(self, deck_id, n=12):
        for i in range(n):
            gid = f"{deck_id}-{i}"
            stats.append({"t": "start", "v": 1, "id": gid, "at": i, "format": "commander", "online": "guest" if i % 5 == 4 else None,
                          "players": 2 + i % 3, "deck": {"id": deck_id, "name": "Kinnan NBC (sample)", "commanders": ["Kinnan"]},
                          "opponents": [{"name": f"AI {k}", "ai": True, "deck": {"name": f"Opponent deck {k + i % 4}"},
                                         "commanders": []} for k in range(1 + i % 3)]})
            result = ("won", "lost", "lost", "conceded", "won", "draw")[i % 6]
            stats.append({"t": "end", "v": 1, "id": gid, "at": i + 0.5, "result": result, "seat": 1 + i % 4, "my_turns": 4 + i % 5,
                          "turn": 20, "eliminations": [],
                          "how": {"cause": ("combat", "alt_win", "commander")[i % 3], "card": "Some Long Card Name of Doom",
                                  "by": "AI 1 (Kinnan NBC)"}})

    def test_the_stats_button_opens_this_decks_page(self):
        gui = self.idle_gui()
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        entry = gui.menu.entry(gui.menu.focus_id)
        self.record(entry.id)
        click(gui, self.button_point(gui, "stats"))
        self.assertIsInstance(gui.modal, stats_view.StatsPage)
        frame(gui, 1)
        titles = [t for _r, t in gui.modal.blocks]
        for want in ("RECORD", "BY NUMBER OF PLAYERS", "BY SEAT (turn order)", "AGAINST", "WHEN YOU WIN", "HOW YOU LOST"):
            self.assertIn(want, titles)
        self.assertEqual(titles.count("RECORD"), 2, "this deck's games against the AI, then its online games")
        self.assertEqual([h for _r, h in gui.modal.headings], ["ONLINE GAMES - a separate record"])
        online_top = gui.modal.headings[0][0].top
        self.assertTrue(all(r.bottom <= online_top for r, t in gui.modal.blocks[:titles.index("RECORD", 1)]),
                        "the online games come after all of the others")
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)

    def test_the_page_fits_at_every_size(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0),
                            ((4096, 2019), 1.75)):
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
                entry = gui.menu.entry(gui.menu.focus_id)
                if not os.path.exists(stats.stats_path()):
                    self.record(entry.id)
                gui.menu.open_stats(gui)
                frame(gui, 1)
                page = gui.modal
                self.assertTrue(page.blocks)
                for i, (a, ta) in enumerate(page.blocks):
                    self.assertLessEqual(a.right, page.area.right - 10, ta)          # clear of the scroll bar
                    self.assertGreaterEqual(a.x, page.area.x, ta)
                    for b, tb in page.blocks[i + 1:]:
                        self.assertFalse(a.colliderect(b), (ta, tb))
                for _ in range(80):                          # the last section can be scrolled into view
                    page.wheel(gui, -1)
                frame(gui, 1)
                last = max(r.bottom for r, _t in page.blocks)
                self.assertLessEqual(last, page.area.bottom + 2)
                gui.modal = None

    def test_all_decks_and_an_empty_deck(self):
        gui = self.idle_gui()
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        gui.menu.open_stats(gui)
        frame(gui, 1)
        self.assertEqual([t for _r, t in gui.modal.blocks], ["RECORD"], "no games yet: one line saying so")
        self.record("some-other-deck", 3)
        gui.modal.games = stats.load()
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "tab_all"))
        self.assertEqual(gui.modal.mode, "all")
        self.assertEqual([t for _r, t in gui.modal.blocks], ["YOUR DECKS (won-lost, games played to the end)"])

    def test_the_deck_screen_keeps_its_buttons_apart(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
                rects = [r for r, k, d in gui.hits if k == "button" and d.get("name") in ("import", "remove", "card_art", "stats")]
                for i, a in enumerate(rects):
                    for b in rects[i + 1:]:
                        self.assertFalse(a.colliderect(b))
                    self.assertTrue(gui.screen.get_rect().contains(a))


class SoakBotTests(unittest.TestCase):
    """Found by patch 43's Brawl soak (6 Oct): Goblin Rabblemaster's goblins must attack; the bot had declared some attackers,
    Forge refused, and with Cancel reading "Call Back" (not "Alpha Strike") the bot pressed OK 28,000 times in 30 minutes."""

    def test_call_back_then_alpha_strike_then_ok(self):
        import soak_bot
        with open(os.path.join(BASE, "tests", "fixtures", "soak", "p44_rabblemaster_callback.json"), encoding="utf-8") as f:
            fx = json.load(f)
        q = fx["asked_again"]
        self.assertEqual((q["input"], q["prompt"]["cancel"]["label"]), ("InputAttack", "Call Back"))
        bot, mem = soak_bot.SoakBot(seed=5), {"infos": [dict(fx["message"])]}
        self.assertEqual(bot.next_action(q, [], mem), ("cancel",), "take the declared attackers back")
        alpha = copy.deepcopy(q)
        alpha["prompt"]["cancel"]["label"] = "Alpha Strike"
        alpha["inputSeq"] += 2
        self.assertEqual(bot.next_action(alpha, [], mem), ("cancel",), "then Alpha Strike")
        done = copy.deepcopy(q)
        done["inputSeq"] += 4
        self.assertEqual(bot.next_action(done, [], mem), ("ok",), "then OK")
        mem["infos"].append(dict(fx["message"]))               # refused again: the same three steps, not OK for ever
        again = copy.deepcopy(q)
        again["inputSeq"] += 6
        self.assertEqual(bot.next_action(again, [], mem), ("cancel",))


    def test_a_huge_record_is_not_read_back(self):
        from tools import soak
        soak._load_modules()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "record.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(50):
                f.write(json.dumps({"t": "state", "turn": i, "pad": "x" * 200}) + "\n")
        self.assertEqual(len(soak._load_messages(path)), 50)
        with mock.patch.object(soak, "RECORD_JUDGE_LIMIT", 1000):
            self.assertIsNone(soak._load_messages(path))
            result = soak.GameResult(0, 1, 2)
            session = mock.Mock(stderr_path=os.path.join(d, "none.log"))
            soak._finish_report(result, session, path, d, d)
            self.assertEqual([f["rule"] for f in result.findings], ["huge_record"])


# ---- sources and folders ----------------------------------------------------------------------------------------------------

class SourceTests(unittest.TestCase):
    def test_the_bridge_reports_forges_outcome(self):
        with open(os.path.join(BRIDGE, "Outcome.java"), encoding="utf-8") as f:
            src = f.read()
        for want in ("getStartingPlayer()", "po.lossState", "po.altWinSourceName", "po.loseConditionSpell", "getTurnsPlayed()",
                     "catch (RuntimeException e)"):
            self.assertIn(want, src)
        with open(os.path.join(BRIDGE, "BridgeGui.java"), encoding="utf-8") as f:
            self.assertIn('m.add("result", Outcome.describe(', f.read())
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            self.assertIn("forge/bridge/Outcome.class", z.namelist())

    def test_the_stats_folder(self):
        with mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": "/somewhere"}):
            self.assertEqual(paths.stats_dir(), os.path.join("/somewhere", "stats"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANTICORE_DATA_DIR", None)
            with mock.patch("paths.user_dir", return_value="/home/me"):
                self.assertEqual(paths.stats_dir(), os.path.join("/home/me", "stats"))
        import tools.export_public as ep
        self.assertIn("stats", ep.TOP_LEVEL_SKIP)
        import tools.nightly_package as nightly
        self.assertIn("stats/", nightly.NEVER)
        self.assertFalse(stats.FILE.endswith(".jsonl"), "backup.py skips *.jsonl")

    def test_the_version(self):
        import version
        self.assertGreaterEqual(tuple(int(x) for x in version.VERSION.split(".")), (0, 28, 48))


# ---- live: real Forge ---------------------------------------------------------------------------------------------------------

DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(Tmp):
    def play(self, extra, act, seconds=30):
        import card_check as cc
        chk = cc.Checker(DECK, say=lambda *a: None, classic_stops=False)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        n = s.setups_done
        given = {e.split("=", 1)[0] for e in extra}
        base = [e for e in ("humanlife=40", "ailife=40") if e.split("=", 1)[0] not in given]   # a life left out is 0
        s.setup(["activeplayer=human", "activephase=MAIN1", "ailibrary=Plains;Plains;Plains"] + base + extra)
        end = time.time() + 30
        while time.time() < end and s.setups_done <= n:
            s.poll()
            time.sleep(0.05)
        chk.pump(1.0)
        s.events.clear()
        rec = self.rec(opponent_decks=[])
        rec.note_state(s.state)
        act(s)
        end = time.time() + seconds
        while time.time() < end and not s.game_over:
            s.poll()
            st = s.state or {}
            rec.note_state(st)
            while s.events:
                rec.note_event(s.events.popleft()[0])
            if st.get("asking"):
                inp = st.get("input") or ""
                if inp.startswith("InputPayMana") or (inp == "InputPassPriority" and st.get("stack")):
                    s.ok()
                elif inp == "InputSelectTargets":
                    s.click_player(s.opponents()[0]["id"])
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            time.sleep(0.05)
        self.assertTrue(s.game_over, "the game never ended")
        while s.events:
            rec.note_event(s.events.popleft()[0])
        return rec.finish((s.game_over_info or {}).get("result"), s.state)

    @staticmethod
    def cast(name):
        return lambda s: s.click_card(next(c for c in s.me()["zones"]["hand"] if c["name"] == name)["id"])

    def test_a_lightning_bolt_win(self):
        end = self.play(["ailife=3", "humanlibrary=Island;Island", "humanbattlefield=Mountain;Mountain", "humanhand=Lightning Bolt"],
                        self.cast("Lightning Bolt"))
        self.assertEqual((end["result"], end["source"], end["seat"]), ("won", "forge", 1))
        self.assertEqual(end["how"], {"cause": "damage", "card": "Lightning Bolt", "by": "Checker", "mine": True})

    def test_a_loss_to_kambals_drain(self):
        end = self.play(["humanlife=2", "humanlibrary=Island;Island;Island;Island", "humanbattlefield=Island;Island;Island",
                         "humanhand=Divination", "aibattlefield=Kambal, Consul of Allocation;Plains;Plains"], self.cast("Divination"))
        self.assertEqual(end["result"], "lost")
        self.assertEqual(end["how"], {"cause": "life_loss", "card": "Kambal, Consul of Allocation", "by": "AI 1 (Filler)"})

    def test_a_thassas_oracle_win(self):
        end = self.play(["humanlibrary=Island", "humanbattlefield=Island;Island;Island;Island", "humanhand=Thassa's Oracle"],
                        self.cast("Thassa's Oracle"))
        self.assertEqual((end["result"], end["how"]["cause"], end["how"]["card"]), ("won", "alt_win", "Thassa's Oracle"))

    def test_a_concede(self):
        end = self.play(["humanlibrary=Island;Island"], lambda s: s.concede())
        self.assertEqual((end["result"], end["how"]["cause"]), ("conceded", "conceded"))


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveOutTests(unittest.TestCase):
    """The online soak of 6 Oct, game 13: a seat conceded and then clicked a card of its own; the card was already out of the
    game and Forge logged "findByView ... not found in any zone". The bridge now drops a click from a seat that is out."""

    def test_a_click_after_conceding_in_a_pod_is_dropped(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        s = fc.ForgeSession(mine, [opp, opp], name="Tester", seed=4, dev=True)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        import card_check as cc
        end = time.time() + 150
        while time.time() < end:
            s.poll()
            st = s.state or {}
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif st.get("asking") and st.get("input") == "InputPassPriority" and st.get("activePlayer") == st.get("me") \
                    and st.get("phase") == "MAIN1":
                break
            elif st.get("asking") and (st.get("prompt") or {}).get("ok", {}).get("enabled") and st.get("input") != "InputPassPriority":
                s.ok()
                time.sleep(0.3)
            elif st.get("asking") and st.get("input") == "InputPassPriority":
                s.ok()
                time.sleep(0.3)
            time.sleep(0.05)
        self.assertEqual((s.state or {}).get("phase"), "MAIN1", "never reached my main phase")
        card = s.me()["zones"]["hand"][0]
        s.concede()
        time.sleep(0.5)
        s.click_card(card["id"])
        end = time.time() + 10
        while time.time() < end and not any(d.get("reason") == "out" for d in s.dropped):
            s.poll()
            time.sleep(0.05)
        self.assertTrue(any(d.get("reason") == "out" for d in s.dropped), s.dropped)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("findByView", f.read())


if __name__ == "__main__":
    unittest.main()
