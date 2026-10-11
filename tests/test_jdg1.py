# SPDX-License-Identifier: GPL-3.0-or-later
"""Round JDG1: the Judge?! window - a card's Oracle text and official rulings, nothing made up (spec: claude/SONNET_SPEC_NOTES_2026-10-08.md
section E). Offline: Scryfall's real answers for three cards (tests/fixtures/judge/scryfall.json, fetched 10 Oct) stand in for the
network. One live test with MANTICORE_LIVE_NETWORK=1."""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import pygame

import forge_table as ft
import judge_dialog as jdlg
import judge_text as jt
import rulings as rul
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import click, frame, key, move, point_for_card

FIX = json.load(open(os.path.join(HERE, "tests", "fixtures", "judge", "scryfall.json"), encoding="utf-8"))
SIZES = (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((4096, 2019), 1.75))


class Isolated(unittest.TestCase):
    """Every test: an empty rulings cache in a temp folder, nothing remembered, no real network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = [mock.patch.object(rul, "folder", lambda: self.tmp),
                        mock.patch.object(rul, "FETCH", self.no_network)]
        for p in self.patches:
            p.start()
        rul.reset()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("MANTICORE_OFFLINE", None)

    def tearDown(self):
        self.env.stop()
        for p in self.patches:
            p.stop()
        rul.reset()

    def no_network(self, uri):
        raise AssertionError("a test reached for the network: " + uri)

    def wait_jobs(self, limit=5.0):
        end = time.time() + limit
        while rul._jobs and time.time() < end:
            time.sleep(0.01)
        self.assertFalse(rul._jobs, "the fetch never finished")


# ---- the words -------------------------------------------------------------------------------------------------------------
class TextTests(Isolated):
    def test_every_face_of_a_double_faced_card(self):
        delver = FIX["delver"]["card"]
        blocks = jt.page({"name": "Delver of Secrets"}, delver, rul.OK, [])
        faces = [b[1] for b in blocks if b[0] == "face"]
        self.assertEqual(faces, ["Delver of Secrets   {U}", "Insectile Aberration"])
        texts = " ".join(b[1] for b in blocks if b[0] == "text")
        self.assertIn("At the beginning of your upkeep, look at the top card of your library", texts)
        self.assertIn("Flying", texts)                                   # the back face's own text
        self.assertEqual([b[1] for b in blocks if b[0] == "pt"], ["1/1", "3/2"])
        self.assertEqual([b[1] for b in blocks if b[0] == "type"],
                         ["Creature — Human Wizard", "Creature — Human Insect"])

    def test_resolves_2nd_of_3(self):
        item = {"trigger": True, "ability": True, "text": "Whenever X, Y. [Tapped: X (5)]", "activator": 2}
        blocks = jt.page({"name": "X"}, None, rul.LOADING, [], (1, 3, item), "AI 1", ["You", "Llanowar Elves"])
        subs = [b[1] for b in blocks if b[0] == "sub"]
        self.assertEqual(subs, ["On the stack: resolves 2nd of 3.", "Put there by: AI 1 (trigger)", "Targets: You, Llanowar Elves"])
        self.assertIn(("text", "Whenever X, Y."), blocks)                # the trigger's own words, without Forge's [Tapped: ...] note
        self.assertEqual(jt.stack_line(0, 3), "On the stack: resolves 1st of 3 (next).")
        self.assertEqual(jt.stack_line(0, 1), "On the stack: the only item - it resolves next.")

    def test_ordinals(self):
        self.assertEqual([jt.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111, 112)],
                         ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th", "112th"])

    def test_rulings_newest_first_with_their_dates_and_source(self):
        thassa = FIX["thassa"]
        blocks = jt.page({"name": "Thassa's Oracle"}, thassa["card"], rul.OK, rul.newest_first(thassa["rulings"]))
        rows = [b for b in blocks if b[0] == "ruling"]
        self.assertEqual(len(rows), 6)
        dates = [r[1] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertTrue(all(r[2] == "Wizards" for r in rows))           # source "wotc"
        self.assertIn(("head", "Rulings (6)"), blocks)
        self.assertTrue(any("If you have no cards in your library, you win the game." in r[3] for r in rows))

    def test_newest_first_keeps_scryfalls_order_within_a_date(self):
        # the three fixture cards' real rulings, mixed: 2011 (Delver), 2020 (Thassa), 2024 (Doubling Season)
        mixed = FIX["delver"]["rulings"] + FIX["thassa"]["rulings"][:2] + FIX["doubling"]["rulings"][:2]
        out = rul.newest_first(mixed)
        self.assertEqual([r["published_at"] for r in out], ["2024-11-08"] * 2 + ["2020-01-24"] * 2 + ["2011-09-22"])
        self.assertEqual(out[:2], FIX["doubling"]["rulings"][:2])
        rul.write_cache("mixed", mixed)
        self.assertEqual(rul.get({"id": "mixed", "name": "Mixed"})[1], out)       # what the window gets is sorted too

    def test_no_rulings_and_every_status_line(self):
        def notes(status, **kw):
            return [b[1] for b in jt.page({"name": "X"}, kw.pop("scry", {"name": "X", "id": "1"}), status, [], **kw)
                    if b[0] == "note"]
        self.assertEqual(notes(rul.OK), ["No official rulings for this card."])
        self.assertEqual(notes(rul.LOADING), ["Rulings not loaded yet."])
        self.assertEqual(notes(rul.OFFLINE), ["Rulings not cached (offline)."])
        self.assertEqual(notes(rul.FAILED), ["Rulings couldn't be loaded from Scryfall."])
        self.assertEqual(notes(rul.NO_CARD, scry=None), ["Rulings not loaded yet."])
        self.assertEqual(notes(rul.NO_CARD, scry=None, offline=True), ["Rulings not cached (offline)."])

    def test_a_token_gets_forges_text_and_says_it_has_no_rulings(self):
        token = {"name": "Treasure", "token": True, "type": "Token Artifact - Treasure",
                 "text": "{T}, Sacrifice this artifact: Add one mana of any color."}
        blocks = jt.page(token, None, rul.NO_CARD, [])
        self.assertIn(("head", "Card text (a token - Forge's text)"), blocks)
        self.assertIn(("text", "{T}, Sacrifice this artifact: Add one mana of any color."), blocks)
        self.assertIn(("note", "A token has no rulings of its own."), blocks)

    def test_nothing_is_made_up(self):
        # every line is the card's own text, one of its rulings, or one of the window's fixed words
        thassa = FIX["thassa"]
        card, found = thassa["card"], rul.newest_first(thassa["rulings"])
        blocks = jt.page({"name": card["name"]}, card, rul.OK, found)
        paragraphs = {p.strip() for p in card["oracle_text"].split("\n") if p.strip()}
        comments = {(r["published_at"], r["comment"]) for r in found}
        fixed = {card["name"], "Oracle text", "Rulings (6)", jt.CREDIT}
        for b in blocks:
            with self.subTest(block=b[:2]):
                if b[0] == "text":
                    self.assertIn(b[1], paragraphs)
                elif b[0] == "ruling":
                    self.assertIn((b[1], b[3]), comments)
                elif b[0] == "face":
                    self.assertEqual(b[1], f"{card['name']}   {card['mana_cost']}")
                elif b[0] == "type":
                    self.assertEqual(b[1], card["type_line"])
                elif b[0] == "pt":
                    self.assertEqual(b[1], f"{card['power']}/{card['toughness']}")
                else:
                    self.assertIn(b[1], fixed)


# ---- rulings: the cache, the background fetch ------------------------------------------------------------------------------
class RulingsTests(Isolated):
    def test_from_the_cache_without_the_network(self):
        thassa = FIX["thassa"]
        rul.write_cache(thassa["card"]["id"], thassa["rulings"])
        status, found = rul.get(thassa["card"])
        self.assertEqual(status, rul.OK)
        self.assertEqual(len(found), 6)

    def test_a_cache_miss_starts_one_background_job_and_never_blocks(self):
        thassa = FIX["thassa"]
        gate, calls = threading.Event(), []

        def slow(uri):
            calls.append(uri)
            gate.wait(5)
            return rul.OK, thassa["rulings"]
        with mock.patch.object(rul, "FETCH", slow):
            t0 = time.perf_counter()
            self.assertEqual(rul.get(thassa["card"]), (rul.LOADING, []))
            self.assertEqual(rul.get(thassa["card"]), (rul.LOADING, []))         # the same card again: no second job
            self.assertLess(time.perf_counter() - t0, 0.2)
            gate.set()
            self.wait_jobs()
        self.assertEqual(calls, [thassa["card"]["rulings_uri"]])
        status, found = rul.get(thassa["card"])
        self.assertEqual((status, len(found)), (rul.OK, 6))
        self.assertEqual(rul.read_cache(thassa["card"]["id"])[1], thassa["rulings"])  # kept on disk for next time

    def test_two_callers_at_once_start_one_fetch(self):
        gate, calls = threading.Event(), []

        def slow(uri):
            calls.append(uri)
            gate.wait(5)
            return rul.OK, []
        with mock.patch.object(rul, "FETCH", slow):
            first = rul._start("x", "https://api.scryfall.com/cards/x/rulings", "X")
            second = rul._start("x", "https://api.scryfall.com/cards/x/rulings", "X")    # e.g. two windows asking in the same moment
            gate.set()
            self.wait_jobs()
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(calls), 1)

    def test_offline_with_and_without_a_cache(self):
        os.environ["MANTICORE_OFFLINE"] = "1"
        thassa, delver = FIX["thassa"], FIX["delver"]
        self.assertEqual(rul.get(thassa["card"]), (rul.OFFLINE, []))
        rul.write_cache(delver["card"]["id"], delver["rulings"])
        self.assertEqual(rul.get(delver["card"])[0], rul.OK)

    def test_no_connection_says_offline_and_one_crashlog_note_never_a_dialog(self):
        thassa, delver = FIX["thassa"], FIX["delver"]
        with mock.patch.object(rul, "FETCH", lambda uri: (rul.OFFLINE, "ConnectionError: no route")), \
                mock.patch("crashlog.note") as note:
            rul.get(thassa["card"])
            self.wait_jobs()
            self.assertEqual(rul.get(thassa["card"]), (rul.OFFLINE, []))      # not asked again for RETRY_SECONDS
            rul.get(delver["card"])
            self.wait_jobs()
        self.assertEqual(note.call_count, 1)
        self.assertIn("Thassa's Oracle", note.call_args[0][1])

    def test_a_bad_answer_is_failed_not_offline(self):
        with mock.patch.object(rul, "FETCH", lambda uri: (rul.FAILED, "HTTP 500")), mock.patch("crashlog.note"):
            rul.get(FIX["doubling"]["card"])
            self.wait_jobs()
            self.assertEqual(rul.get(FIX["doubling"]["card"]), (rul.FAILED, []))

    def test_an_old_cache_is_shown_while_a_newer_list_is_fetched(self):
        thassa = FIX["thassa"]
        rul.write_cache(thassa["card"]["id"], thassa["rulings"][:2], now=time.time() - (rul.CACHE_DAYS + 1) * 86400)
        with mock.patch.object(rul, "FETCH", lambda uri: (rul.OK, thassa["rulings"])):
            status, found = rul.get(thassa["card"])
            self.assertEqual((status, len(found)), (rul.OK, 2))
            self.wait_jobs()
        self.assertEqual(len(rul.get(thassa["card"])[1]), 6)

    def test_a_fresh_cache_is_not_fetched_again(self):
        thassa = FIX["thassa"]
        rul.write_cache(thassa["card"]["id"], thassa["rulings"], now=time.time() - (rul.CACHE_DAYS - 1) * 86400)
        self.assertEqual(rul.get(thassa["card"])[0], rul.OK)                   # no_network would have failed the fetch thread
        self.assertFalse(rul._jobs)

    def test_no_card_data_no_fetch(self):
        self.assertEqual(rul.get(None), (rul.NO_CARD, []))
        self.assertEqual(rul.get({"name": "Treasure"}), (rul.NO_CARD, []))


# ---- the table -----------------------------------------------------------------------------------------------------------
class Store(StubStore):
    """Card data for the cards in the recorded states: Thassa's real Scryfall object under each name asked for."""

    def __init__(self, known=None):
        self.known = {k.lower(): v for k, v in (known or {}).items()}
        self.asked = []

    def peek_card(self, name):
        return self.known.get(name.strip().lower())


def table(state="stack_two_late", size=(1360, 840), scale=1.0, store=None, **modes):
    st = load_state(state) if isinstance(state, str) else state
    gui = ft.ForgeTable(FakeSession(st, load_log()), store or Store(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    for k, v in modes.items():
        setattr(gui, k, v)
    frame(gui, 2)
    return gui


def judge_buttons(gui):
    return [r for r, k, d in gui.hits if k == "button" and d.get("name") == "judge"]


class TableTests(Isolated):
    def setUp(self):
        super().setUp()
        os.environ["MANTICORE_OFFLINE"] = "1"                 # the table never fetches in these tests

    def test_j_on_a_hovered_card(self):
        gui = table("main1_lands")
        card = gui.session.me()["zones"]["battlefield"][0]
        move(gui, point_for_card(gui, card["id"]))
        key(gui, pygame.K_j)
        self.assertIsInstance(gui.modal, jdlg.JudgeDialog)
        self.assertEqual(gui.modal.card["id"], card["id"])
        self.assertIsNone(gui.modal.stack)                   # a permanent gets no stack line

    def test_j_with_nothing_hovered_is_the_top_of_the_stack(self):
        gui = table("stack_two_late")
        move(gui, (5, gui.L.H - 5))
        key(gui, pygame.K_j)
        top = gui.state["stack"][0]
        self.assertEqual(gui.modal.card["id"], top["card"]["id"])
        self.assertEqual(gui.modal.stack[:2], (0, 2))

    def test_j_over_a_stack_row_is_that_item(self):
        # both rows of stack_two_late: City of Brass's trigger (its card is a land on the battlefield) and Basalt Monolith (a spell)
        gui = table("stack_two_late", (1920, 1080))
        rows = [(r, d) for r, k, d in gui.hits if k == "stack"]
        self.assertEqual(len(rows), 2)
        for r, d in rows:
            with self.subTest(card=d["card"]["name"]):
                move(gui, r.center)
                key(gui, pygame.K_j)
                idx = [it.get("key") for it in gui.state["stack"]].index(d["item"].get("key"))
                self.assertEqual(gui.modal.stack[:2], (idx, 2))
                self.assertEqual(gui.modal.who, gui.player_label(d["item"].get("activator")))
                key(gui, pygame.K_ESCAPE)

    def test_the_pinned_card(self):
        gui = table("main1_lands")
        card = gui.session.me()["zones"]["battlefield"][1]
        click(gui, point_for_card(gui, card["id"]), button=3)
        move(gui, (5, gui.L.H - 5))
        key(gui, pygame.K_j)
        self.assertEqual(gui.modal.card["id"], card["id"])

    def test_nothing_is_sent_to_forge_and_ctrl_j_does_nothing(self):
        gui = table("stack_two_late")
        before = list(gui.session.sent)
        key(gui, pygame.K_j, mod=pygame.KMOD_CTRL)
        self.assertIsNone(gui.modal)
        key(gui, pygame.K_j)
        key(gui, pygame.K_END)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.session.sent, before)

    def test_a_question_waits_until_the_judge_closes(self):
        gui = table("main1_lands")
        move(gui, point_for_card(gui, gui.session.me()["zones"]["battlefield"][0]["id"]))
        key(gui, pygame.K_j)
        judge = gui.modal
        self.assertIsInstance(judge, jdlg.JudgeDialog)
        gui.session.handle({"t": "request", "id": 7, "kind": "confirm", "title": "Pay 2 life?", "message": "Pay 2 life?",
                            "options": ["Yes", "No"]})
        frame(gui, 2)
        self.assertIs(gui.modal, judge)
        key(gui, pygame.K_ESCAPE)
        frame(gui, 1)
        self.assertIsNotNone(gui.modal)
        self.assertNotIsInstance(gui.modal, jdlg.JudgeDialog)

    def test_not_on_the_deck_screen_or_under_the_cog(self):
        gui = table("main1_lands")
        gui.overlay = ft.fset.SettingsPopup()
        key(gui, pygame.K_j)
        self.assertNotIsInstance(gui.modal, jdlg.JudgeDialog)

    def test_a_face_down_card_is_refused_with_a_toast(self):
        st = load_state("main1_lands")
        st["players"][0]["zones"]["battlefield"][0]["faceDown"] = True
        gui = table(st)
        cid = gui.session.me()["zones"]["battlefield"][0]["id"]
        move(gui, point_for_card(gui, cid))
        key(gui, pygame.K_j)
        self.assertIsNone(gui.modal)

    def test_the_button_in_the_stacks_heading_then_the_logs(self):
        gui = table("stack_two_late", (1920, 1080))
        [b] = judge_buttons(gui)
        self.assertTrue(gui.L.stack.contains(b))
        self.assertLess(b.bottom, gui.L.stack.y + gui.stack_geometry(gui.L).head)    # in the heading, above the first row
        click(gui, b.center)
        self.assertEqual(gui.modal.stack[:2], (0, 2))                               # the mouse is on the button: the top item
        quiet = table("main1_lands", (1920, 1080))
        [b] = judge_buttons(quiet)
        self.assertTrue(quiet.L.log.contains(b))
        self.assertLess(b.bottom, quiet.L.log.y + quiet.font("small", True).get_height() + 10)

    def test_the_button_goes_on_the_action_bar_with_no_heading_in_the_column(self):
        gui = table("main1_lands", (1920, 1080), log_mode="hidden", preview_mode="over_card")
        [b] = judge_buttons(gui)
        self.assertTrue(gui.L.bar.contains(b))
        others = [r for r, k, d in gui.hits if k == "button" and d.get("name") in ("ok", "cancel", "undo")]
        self.assertFalse(any(b.colliderect(r) for r in others))

    def test_the_button_never_costs_the_action_bar_its_words(self):
        # a long question on a short bar: no room left of the buttons, so no Judge?! there (J still works)
        st = load_state("main1_lands")
        st["prompt"]["message"] = ("Looking at cards in AI 1 (Kinnan, Bonder Prodigy)'s library: Sol Ring, Basalt Monolith, "
                                   "Thrasios, Triton Hero, Birds of Paradise, Llanowar Elves, Fyndhorn Elves")
        st["input"] = ""
        gui = table(st, (1360, 840), log_mode="hidden", preview_mode="over_card")
        self.assertEqual(judge_buttons(gui), [])

    def test_it_fits_at_every_size(self):
        long_card = dict(FIX["thassa"]["card"])
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                rul.write_cache(long_card["id"], FIX["thassa"]["rulings"] * 4)          # 24 rulings: it has to scroll
                gui = table("stack_two_late", size, scale, store=Store({"City of Brass": long_card}))
                move(gui, (5, gui.L.H - 5))
                key(gui, pygame.K_j)
                d = gui.modal
                frame(gui, 1)
                screen = pygame.Rect(0, 0, *size)
                self.assertTrue(screen.contains(d.rect), d.rect)
                self.assertTrue(d.rect.contains(d.viewport), (d.rect, d.viewport))
                [(close, _n)] = d.buttons
                self.assertTrue(d.rect.contains(close))
                self.assertFalse(close.colliderect(d.viewport))
                self.assertGreater(d.max_scroll, 0)
                key(gui, pygame.K_END)
                self.assertEqual(d.scroll, d.max_scroll)
                d.wheel(gui, -50)
                self.assertEqual(d.scroll, d.max_scroll)
                key(gui, pygame.K_HOME)
                self.assertEqual(d.scroll, 0)
                d.wheel(gui, 50)
                self.assertEqual(d.scroll, 0)
                click(gui, close.center)
                self.assertIsNone(gui.modal)
                gui.close() if hasattr(gui, "close") else None

    def test_rulings_offline_from_the_cache_through_the_table(self):
        thassa = FIX["thassa"]
        rul.write_cache(thassa["card"]["id"], thassa["rulings"])
        gui = table("stack_two_late", store=Store({"City of Brass": thassa["card"]}))
        key(gui, pygame.K_j)
        blocks = gui.modal.blocks(gui)
        self.assertEqual(len([b for b in blocks if b[0] == "ruling"]), 6)

    def test_card_data_not_loaded_is_asked_for_once(self):
        os.environ.pop("MANTICORE_OFFLINE", None)
        with mock.patch("legality.ensure_card_data") as ensure:
            gui = table("main1_lands")
            move(gui, point_for_card(gui, gui.session.me()["zones"]["battlefield"][0]["id"]))
            key(gui, pygame.K_j)
            frame(gui, 3)
        self.assertEqual(ensure.call_count, 1)                  # once, not once a frame
        self.assertIsInstance(gui.modal, jdlg.JudgeDialog)

    def test_nothing_to_look_up_says_so(self):
        gui = table("main1_lands")
        move(gui, (5, gui.L.H - 5))
        key(gui, pygame.K_j)                                    # no card under the mouse, none pinned, an empty stack, nothing shown yet
        self.assertIsNone(gui.modal)

    def test_help_lists_j(self):
        self.assertTrue(any(k.startswith("J ") for k, _t in ft.HELP_LINES))
        with open(os.path.join(HERE, "README.txt"), encoding="utf-8") as f:
            self.assertIn("Judge?!", f.read())


@unittest.skipUnless(os.environ.get("MANTICORE_LIVE_NETWORK") == "1", "live Scryfall: set MANTICORE_LIVE_NETWORK=1")
class LiveTests(Isolated):
    def test_two_cards_rulings_from_scryfall(self):
        import requests
        import card_data
        with mock.patch.object(rul, "FETCH", rul._http_fetch):
            counts = {}
            for name in ("Thassa's Oracle", "Doubling Season"):
                card = requests.get(f"{rul.API}/cards/named", params={"exact": name}, headers=card_data.HEADERS, timeout=10).json()
                time.sleep(0.1)
                rul.get(card)
                self.wait_jobs(15)
                status, found = rul.get(card)
                self.assertEqual(status, rul.OK)
                counts[name] = len(found)
            print("\nlive rulings:", counts)
            self.assertGreater(counts["Thassa's Oracle"], 0)


if __name__ == "__main__":
    unittest.main()
