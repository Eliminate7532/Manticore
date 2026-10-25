# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG14: people, the dialogue box, quests, the inn, the duel hall, the pause window, the restyled corner and result windows
(claude/SPEC_RPG_SLICE_2026-10-23.md, RPG14).

The spec's checks: every NPC's dialogue exists; every quest can be finished (a scripted run, through the window); the windows fit
at 5 sizes; the keyboard reaches every button; the inn sets the return point; the hall lights after the boss. Plus: the words
fit the real box (the dialogue lane's open question 8), no entry hides behind an earlier one, what talk.check catches, the
conversation rules on their own, typing at 40 characters a second, the mouse in the box and the menu, talking across a counter,
Platino can be found, the boss's words before and after his duel, the Collection.
"""
import json
import os
import time
import unittest
from unittest import mock

import pygame

import gfx
import rpg_encounter as renc
import rpg_screens as rs
import rpg_ui
from rpg import clock, conditions
from rpg import adventure as adv
from rpg import encounters, explore, talk
from tests import rpg_route
from tests import test_rpg8 as r8
from tests import test_rpg13 as r13
from tests.test_forge_table import click, frame, key

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = ((1024, 640), (1366, 768), (1920, 1080), (2560, 1440), (4096, 2019))
TOWN = "lantern_hollow"
STEPS = {(0, 1): "down", (0, -1): "up", (1, 0): "right", (-1, 0): "left"}


def kinds():
    with open(os.path.join(BASE, "rpg", "data", "enemy_kinds.json"), encoding="utf-8") as f:
        return {k["id"] for k in json.load(f)["kinds"]}


def map_people():
    out = {}
    for name in ("lantern_hollow", "ashfen_outskirts", "warrens_1", "warrens_2"):
        out.update(explore.load_area(name).npcs)
    return out


def every_page():
    """[(where, page)] - every page a dialogue box can show."""
    out = []
    for nid, n in talk.npcs().items():
        for i, e in enumerate(n["lines"]):
            out += [(f"{nid} entry {i + 1}", p) for p in e["pages"]]
    for qid, q in talk.quests().items():
        for k in ("offer", "accepted", "declined", "progress", "ready", "done"):
            out += [(f"{qid} {k}", p) for p in q[k]]
    for k in ("rest", "woke"):
        out += [(f"inn {k}", p) for p in talk.inn()[k]]
    with open(os.path.join(BASE, "rpg", "data", "kinds_text.json"), encoding="utf-8") as f:
        said_by_kind = json.load(f)["kinds"]
    for kid, k in said_by_kind.items():
        for key_ in ("intro", "defeated"):
            out += [(f"{kid} {key_}", p) for p in k.get(key_, ())]
    out.append(("rematch", talk.REMATCH_PAGE))
    return out


def talk_spot(area, nid, start, at=None, walls=()):
    """(tile, facing) to talk to `nid` from: next to them, or across a counter; reachable from `start` without stepping on a
    trigger. None when there is none. Round RPG16: at - the person's tile now (a person may have a day and a night place;
    default their first marker); walls - the tiles of the people who come and go, there now."""
    col, row = at or area.npcs[nid]["tile"]
    avoid = rpg_route.trigger_tiles(area) | set(walls)
    for (dx, dy), facing in STEPS.items():
        for reach in (1, 2):                                  # 2: across a counter (Round RPG15b: a counter tile, never a wall)
            tile = (col - dx * reach, row - dy * reach)
            if reach == 2 and not area.is_counter(col - dx, row - dy):
                continue
            if area.blocked(*tile) or tile in avoid:
                continue
            if rpg_route.tile_route(area, start, tile, avoid=avoid) is not None:
                return tile, facing
    return None


class DataTests(unittest.TestCase):
    """The words (rpg/data: npcs, quests, inn, kinds_text - the dialogue lane's draft) against the maps and the box."""

    def test_every_person_on_the_map_has_words_and_every_word_a_person(self):
        people = map_people()
        self.assertEqual(sorted(people), sorted(talk.npcs()))
        self.assertEqual(talk.check(kinds=kinds(), map_npcs=people), [])

    def test_every_page_fits_the_dialogue_box(self):
        pygame.init()
        cw = rpg_ui.font().size("M")[0]
        width = rpg_ui.LINE_CHARS * cw                       # the box's text column (DialogueBox.draw)
        pages = every_page()
        self.assertGreater(len(pages), 100)
        for where, page in pages:
            lines = rpg_ui.wrap_lines(page)
            self.assertLessEqual(len(lines), rpg_ui.LINES, (where, page, lines))
            for ln in lines:
                self.assertLessEqual(rpg_ui.font().size(ln)[0], width, (where, ln))

    def test_no_entry_hides_behind_an_earlier_one(self):
        """An entry whose conditions include all of an earlier entry's could never be said (the earlier one always wins)."""
        for nid, n in talk.npcs().items():
            seen = []
            for i, e in enumerate(n["lines"]):
                when = set(e["when"])
                for j, earlier in seen:
                    self.assertFalse(earlier <= when and earlier != when or earlier == when,
                                     f"{nid}: entry {i + 1} can't be reached past entry {j + 1}")
                seen.append((i, when))

    def test_the_answers_fit_the_choice_window(self):
        for qid, q in talk.quests().items():
            for k in ("yes", "no"):
                self.assertLessEqual(len(q[k]), 24, (qid, k))
        self.assertLessEqual(max(len(talk.REMATCH_YES), len(talk.REMATCH_NO)), 24)

    def test_check_catches_each_kind_of_mistake(self):
        people = json.loads(json.dumps(talk.npcs()))
        quests = json.loads(json.dumps(talk.quests()))
        inn = json.loads(json.dumps(talk.inn()))
        people["elder"]["lines"][0]["pages"][0] = "word " * 40                               # four lines and more
        people["lamplighter"]["lines"][0]["when"] = ["flag:met_lamplighter", "mood:sad"]       # no such condition
        people["gate_guard"]["lines"][0]["when"] = ["flag:priest_dead"]                         # a flag nothing sets
        people["card_seller"]["lines"][-1]["when"] = ["flag:warrens_entered"]                   # no fallback last
        quests["lamplighters_debt"]["giver"] = "lamp_lighter"                                   # a giver with no words
        quests["lamplighters_debt"]["goal"] = {"beat": "fire_imp", "count": 3}                  # no such foe
        inn["woke"] = ["a"] * 5                                                                 # five pages
        on_map = dict(map_people(), stranger={"name": "A stranger", "tile": (0, 0)})            # nobody's words
        found = "\n".join(talk.check(people, quests, inn, kinds=kinds(), map_npcs=on_map))
        for bit in ("elder entry 1: a page of", "unknown condition 'mood:sad'", "flag 'priest_dead' is never set",
                    "card_seller: the last entry must have no conditions", "giver 'lamp_lighter' has no words",
                    "no enemy kind 'fire_imp'", "inn woke: 5 pages", "stranger stands on the map but has no words"):
            self.assertIn(bit, found)


class ConversationTests(unittest.TestCase):
    """rpg.talk on its own: who says what, and what a talk changes in the save's world."""

    def test_the_first_talk_says_the_first_meeting_words_once(self):
        w = {}
        c = talk.Conversation("gate_guard", w)
        self.assertEqual(c.pages[0], talk.npcs()["gate_guard"]["lines"][0]["pages"][0])
        self.assertIn("met_gate_guard", talk.flags(w))
        again = talk.Conversation("gate_guard", w)
        self.assertEqual(again.pages, talk.npcs()["gate_guard"]["lines"][-1]["pages"])          # the fallback now

    def test_an_offer_then_yes_then_progress(self):
        w, q = {}, talk.quests()["the_dark_hall"]
        c = talk.Conversation("elder", w)
        hesk = talk.npcs()["elder"]["lines"][0]["pages"]
        self.assertEqual(c.pages, hesk + q["offer"])                                         # first meeting, then the offer
        self.assertEqual(c.choice, (q["yes"], q["no"]))
        self.assertEqual(c.answer(True), q["accepted"])
        self.assertEqual(talk.quest_state(w, "the_dark_hall"), "active")
        self.assertEqual(talk.Conversation("elder", w).pages, q["progress"])

    def test_declining_leaves_the_offer_for_next_time(self):
        w, q = {}, talk.quests()["lamplighters_debt"]
        c = talk.Conversation("lamplighter", w)
        self.assertEqual(c.answer(False), q["declined"])
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "none")
        c2 = talk.Conversation("lamplighter", w)
        self.assertEqual(c2.pages, q["offer"])                                               # no first meeting the second time
        self.assertIsNotNone(c2.choice)

    def test_the_dark_hall_is_finished_by_the_boss(self):
        w, q = {}, talk.quests()["the_dark_hall"]
        talk.Conversation("elder", w).answer(True)
        talk.on_duel_won(w, "grave_priest", "regular")                                       # not the boss duel: no flag
        self.assertNotIn(talk.BOSS_FLAG, talk.flags(w))
        talk.on_duel_won(w, "grave_priest", "boss")
        self.assertEqual(talk.quest_state(w, "the_dark_hall"), "ready")
        c = talk.Conversation("elder", w)
        self.assertEqual(c.pages, q["ready"])
        self.assertEqual(c.reward, {"flag": "has_orrins_map", "gold": 50})
        self.assertEqual(c.after, q["done"])
        self.assertEqual(talk.quest_state(w, "the_dark_hall"), "done")
        self.assertIn("has_orrins_map", talk.flags(w))
        done = [e for e in talk.npcs()["elder"]["lines"] if e["when"] == ["quest:the_dark_hall=done"]][0]["pages"]
        self.assertEqual(talk.Conversation("elder", w).pages, done)

    def test_the_lamplighters_debt_counts_three_bandits(self):
        w = {}
        talk.on_duel_won(w, "ember_bandit", "regular")                                       # before the quest: not counted
        talk.Conversation("lamplighter", w).answer(True)
        for _ in range(2):
            talk.on_duel_won(w, "ember_bandit", "regular")
        talk.on_duel_won(w, "plague_rat", "dungeon")
        self.assertEqual(talk.journal(w)[0][1][-8:], "(2 of 3)")
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "active")
        talk.on_duel_won(w, "ember_bandit", "regular")
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "ready")
        c = talk.Conversation("lamplighter", w)
        self.assertEqual((c.reward, c.after), ({"card": "rare_from_band"}, talk.quests()["lamplighters_debt"]["done"]))
        self.assertEqual(talk.journal(w)[0][2], "done")

    def test_the_innkeeper_rests_you(self):
        """Round RPG15c (review R3, expected change): she asks first - "Sleep until morning" says the inn's rest words and rests
        you; "Not now" doesn't (before, every word with her was a night's sleep)."""
        c = talk.Conversation(talk.INNKEEPER, {"clock": 21 * 60})      # Round RPG17: at night (a new game is at 07:00, by day)
        self.assertFalse(c.rest)
        self.assertEqual(c.choice, (talk.REST_YES, talk.REST_NO))
        self.assertNotIn(talk.inn()["rest"][0], c.pages)
        self.assertEqual(c.answer(True), talk.inn()["rest"])
        self.assertTrue(c.rest)
        c = talk.Conversation(talk.INNKEEPER, {})
        self.assertEqual(c.answer(False), [])
        self.assertFalse(c.rest)

    def test_the_hall_keeper_offers_rematches_once_the_boss_is_beaten(self):
        w = {}
        self.assertIsNone(talk.Conversation(talk.HALL_KEEPER, w).choice)
        talk.set_flag(w, talk.BOSS_FLAG)
        c = talk.Conversation(talk.HALL_KEEPER, w)
        self.assertEqual(c.choice, (talk.REMATCH_YES, talk.REMATCH_NO))
        self.assertEqual(c.pages[-1], talk.REMATCH_PAGE)
        c.answer(True)
        self.assertTrue(c.rematch)


class BoxTests(unittest.TestCase):
    """rpg_ui's dialogue box and cursor menu on their own."""

    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_typing_at_40_a_second_enter_shows_the_rest_then_turns_the_page(self):
        b = rpg_ui.DialogueBox("Pell", ["One two three four five six seven eight nine ten.", "Second page."], 100.0)
        self.assertEqual(b.typed(100.5), 20)
        self.assertFalse(b.complete(100.5))
        self.assertIsNone(b.key(pygame.K_RETURN, 100.5))                 # Enter: the whole page at once
        self.assertTrue(b.complete(100.5))
        self.assertTrue(b.more())
        self.assertIsNone(b.key(pygame.K_RETURN, 100.6))                 # the next page
        self.assertEqual(b.page, 1)
        self.assertEqual(b.typed(100.6), 0)
        b.key(pygame.K_RETURN, 100.7)
        self.assertEqual(b.key(pygame.K_RETURN, 100.8), "close")

    def test_the_down_mark_shows_only_when_more_follows(self):
        screen = pygame.Surface((1920, 1080))
        b = rpg_ui.DialogueBox("Pell", ["Short.", "More."], 0.0)
        b.advance(0.0)                                                   # typed out
        with mock.patch.object(pygame.draw, "polygon") as tri:
            b.draw(screen, 2.0)                                          # 2.0 s: the mark's "on" half
            self.assertTrue(tri.called)
        b.advance(2.0)                                                   # the last page: no more
        b.advance(2.0)
        with mock.patch.object(pygame.draw, "polygon") as tri:
            b.draw(screen, 4.0)
            self.assertFalse(tri.called)

    def test_a_choice_by_keys_esc_is_the_second_answer(self):
        b = rpg_ui.DialogueBox("Hesk", ["Will you go?"], 0.0, choice=("I'll go down.", "Not yet."))
        b.advance(0.0)
        b.advance(0.0)                                                   # the answers open
        self.assertTrue(b.choosing)
        b.key(pygame.K_DOWN, 0.0)
        b.key(pygame.K_UP, 0.0)
        self.assertEqual(b.key(pygame.K_RETURN, 0.0), "answer")
        self.assertTrue(b.answer)
        b2 = rpg_ui.DialogueBox("Hesk", ["Will you go?"], 0.0, choice=("I'll go down.", "Not yet."))
        b2.advance(0.0)
        self.assertEqual(b2.key(pygame.K_ESCAPE, 0.0), "answer")
        self.assertFalse(b2.answer)

    def test_the_mouse_answers_too(self):
        screen = pygame.Surface((1366, 768))
        b = rpg_ui.DialogueBox("Hesk", ["Will you go?"], 0.0, choice=("I'll go down.", "Not yet."))
        b.draw(screen, 0.1)
        self.assertEqual([n for _r, n in b.buttons], ["talk_next"])
        b.pick("talk_next", 5.0)                                         # typed out by now: the answers open
        b.draw(screen, 5.0)
        self.assertEqual([n for _r, n in b.buttons], ["talk_next", "talk_yes", "talk_no"])
        self.assertEqual(b.pick("talk_no", 5.0), "answer")
        self.assertFalse(b.answer)

    def test_the_menu_keeps_blank_lines_and_never_runs_off_the_screen(self):
        screen = pygame.Surface((1024, 640))
        info = [("Karl", gfx.DB_GOLD), ("", gfx.DB_DIM), ("Quests", gfx.DB_GOLD)] + [("A line", gfx.DB_TEXT)] * 80
        m = rpg_ui.ListMenu("Menu", rs.PAUSE_ITEMS, info)
        drawn = []
        real = rpg_ui.text

        def spy(surface, s, x, y, colour, px=rpg_ui.FONT_PX, anchor="topleft"):
            r = real(surface, s, x, y, colour, px, anchor)
            drawn.append((surface.get_size(), s, r))
            return r
        with mock.patch.object(rpg_ui, "text", spy):
            m.draw(screen)
        info_lines = [(size, s, r) for size, s, r in drawn if s in ("Karl", "Quests", "A line")]
        karl = next(r for _z, s, r in info_lines if s == "Karl")
        quests = next(r for _z, s, r in info_lines if s == "Quests")
        self.assertEqual(quests.y - karl.y, 2 * rpg_ui.PITCH_UI)                  # the blank line is kept
        for size, s, r in drawn:
            self.assertTrue(pygame.Rect((0, 0), size).contains(r), (s, r, size))   # every line inside its window
        for r, _n in m.buttons:
            self.assertTrue(screen.get_rect().contains(r))

    def test_the_menu_by_keys_wraps_round_and_skips_nothing(self):
        m = rpg_ui.ListMenu("Menu", rs.PAUSE_ITEMS)
        got = []
        for _ in rs.PAUSE_ITEMS:
            got.append(m.key(pygame.K_RETURN))
            m.key(pygame.K_DOWN)
        self.assertEqual(got, [n for n, _l, _o in rs.PAUSE_ITEMS])
        self.assertEqual(m.cursor, 0)                                    # round again
        m.key(pygame.K_UP)
        self.assertEqual(m.cursor, len(rs.PAUSE_ITEMS) - 1)


class WindowBase(r13.ScreenBase):
    def in_town(self, g):
        f = self.on_map(g, TOWN, "start")
        f.game.save.world.setdefault("flags", [])
        return f

    def stand(self, g, nid, walk=False):
        """The walker on the spot to talk to `nid` from, facing them (walk: get there on foot from where it is)."""
        f = g.modal
        ex = f.explorer
        a = ex.area
        here = a.tile_at(ex.walker.x, ex.walker.y)
        at = next((p["tile"] for p in ex.present_people() if p["id"] == nid), None)     # Round RPG16: where they are now
        self.assertIsNotNone(at, f"{nid} isn't out at this hour")
        spot = talk_spot(a, nid, here, at=at, walls=ex.walker.blockers)
        self.assertIsNotNone(spot, f"nowhere to talk to {nid} from")
        tile, facing = spot
        if walk:
            events, _n = rpg_route.walk_to_tile(ex, tile)
            self.assertEqual(events, [])
        else:
            ex._use(a, explore.Walker(a, *rpg_route.middle(a, tile)))
        ex.walker.facing = facing
        frame(g)
        return f

    def say_all(self, g, answer=None):
        """Enter through the box until it closes (or the choice opens: answered with Enter on Yes / Down then Enter on No)."""
        f = g.modal
        seen = []
        for _ in range(60):
            if f.sub != "talk":
                return seen
            box = f.talk_box
            if box.choosing and answer is not None:
                if box.cursor != (0 if answer else 1):       # Round RPG15c: the cursor may start on the second answer
                    key(g, pygame.K_DOWN)
                key(g, pygame.K_RETURN)
                answer = None
                continue
            seen.append(box.pages[box.page])
            key(g, pygame.K_RETURN)                                      # the whole page
            key(g, pygame.K_RETURN)                                      # the next one
        self.fail("the box never closed")

    def win(self, g, foe):
        """A duel with `foe` won through the window (the encounter screen, Duel, the table, the end screen, back)."""
        f = g.modal
        self.meet(g, foe)
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=True)
        g.end_continue()
        frame(g)
        return g.modal


class WindowTests(WindowBase):
    def test_the_windows_fit_at_five_sizes(self):
        r8.new_game(None)
        for size in SIZES:
            with self.subTest(size=size):
                g = self.gui(size=size)
                self.open(g, slot=1)
                f = self.in_town(g)
                whole = g.screen.get_rect()
                self.stand(g, "elder")
                key(g, pygame.K_RETURN)
                self.assertEqual(f.sub, "talk")
                for _ in range(12):                                     # to the offer's answers
                    if f.talk_box.choosing:
                        break
                    key(g, pygame.K_RETURN)
                self.assertTrue(f.talk_box.choosing)
                rects = [r for r, _n in f.buttons]
                box, choice = rects[0], rects[1:]
                self.assertTrue(all(whole.contains(r) for r in rects), rects)
                self.assertTrue(all(not r.colliderect(box) for r in choice))
                key(g, pygame.K_DOWN)
                key(g, pygame.K_RETURN)                                 # "Not yet."
                self.say_all(g)
                key(g, pygame.K_ESCAPE)                                 # the pause window
                self.assertEqual(f.sub, "pause")
                self.assertEqual([n for _r, n in f.buttons], [n for n, _l, _o in rs.PAUSE_ITEMS])
                self.assertTrue(all(whole.contains(r) for r, _n in f.buttons))
                key(g, pygame.K_ESCAPE)
                self.assertTrue(whole.contains(f.hud))
                f.result, f.sub = ("Victory", ["You beat the Ember Bandit: +12 gold.", "New card: Shock."], rs.GREEN), "result"
                frame(g)
                self.assertTrue(whole.contains(f.message_box.rect))
                self.assertEqual([n for _r, n in f.buttons], ["ok"])
                self.assertTrue(f.message_box.rect.contains(f.buttons[0][0]))
                click(g, f.buttons[0][0].center)
                self.assertIsNone(f.sub)
                self.close(g)

    def test_the_pause_window_has_the_specs_items(self):
        self.assertEqual([(n, label) for n, label, _on in rs.PAUSE_ITEMS],
                         [("resume", "Back to the map"), ("hint", "Hint"), ("deck", "Deck"), ("collection", "Collection"),
                          ("save", "Save"), ("light", "Light"), ("settings", "Settings"), ("leave", "Main menu")])
        # RPG15: + Light; RPG15b: + Hint (expected changes)

    def test_the_corner_window_cuts_a_long_deck_name_and_stays_in_half_the_screen(self):
        r8.new_game(None)
        for size in SIZES:
            with self.subTest(size=size):
                g = self.gui(size=size)
                self.open(g, slot=1)
                f = self.in_town(g)
                W, _H = g.screen.get_size()
                rect = f.draw_hud(g, "Lantern Hollow", {"gold": 120, "deck": "A very long deck name " * 12, "cards": 60})
                self.assertLessEqual(rect.w, W // 2)
                self.assertTrue(f.hud_words[-1][0].endswith("..."), f.hud_words[-1][0])
                self.assertTrue(all(rect.contains(r) for _t, r in f.hud_words))
                self.close(g)

    def test_the_keyboard_reaches_every_pause_item(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        seen = set()
        for i, (name, _label, _on) in enumerate(rs.PAUSE_ITEMS):
            if name == "leave":
                continue
            key(g, pygame.K_ESCAPE)
            self.assertEqual(f.sub, "pause")
            for _ in range(i):
                key(g, pygame.K_DOWN)
            key(g, pygame.K_RETURN)
            seen.add(name)
            if name == "resume":
                self.assertEqual((f.stage, f.sub), ("map", None))
            elif name == "deck":
                self.assertEqual(f.stage, "deck")
                key(g, pygame.K_ESCAPE)
            elif name == "collection":
                self.assertEqual(f.stage, "collection")
                key(g, pygame.K_ESCAPE)
            elif name == "save":
                self.assertEqual((f.sub, f.message[0]), (None, "Saved."))
            elif name == "settings":
                self.assertIsNotNone(g.overlay)
                g.overlay = None
                key(g, pygame.K_ESCAPE)
            elif name == "light":                       # Round RPG15: Light on / off - the window stays open (expected change)
                self.assertEqual(f.sub, "pause")
                key(g, pygame.K_ESCAPE)
            elif name == "hint":                        # Round RPG15b: the hint in the dialogue box (expected change)
                self.assertEqual((f.sub, f.talk_box.name), ("talk", "Hint"))
                for _ in range(8):
                    if f.sub != "talk":
                        break
                    key(g, pygame.K_RETURN)
            self.assertEqual(f.stage, "map")
        key(g, pygame.K_ESCAPE)
        for _ in range(len(rs.PAUSE_ITEMS) - 1):
            key(g, pygame.K_DOWN)
        key(g, pygame.K_RETURN)                                         # Main menu: saved and left
        self.assertIsNot(g.modal, f)
        self.assertEqual(seen | {"leave"}, {n for n, _l, _o in rs.PAUSE_ITEMS})

    def test_the_collection_lists_every_card_you_own(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        drawn = []
        real = rs.draw_text

        def spy(screen, s, x, y, font, *a, **k):
            drawn.append(s)
            return real(screen, s, x, y, font, *a, **k)
        with mock.patch.object(rs, "draw_text", spy):
            frame(g)
        coll = f.game.save.collection
        owned = [n for n in coll.names() if isinstance(coll.count(n), int) and coll.count(n) > 0]
        total = sum(coll.count(n) for n in owned)
        self.assertTrue(any(s.startswith(f"{total} cards, {len(owned)} different") for s in drawn), drawn[:5])
        shown = [s for s in drawn if any(s == f"{coll.count(n)}  {n}" for n in owned[:5])]
        self.assertTrue(shown)
        self.press(g, "collection_done")
        self.assertEqual(f.stage, "map")

    def test_talking_needs_someone_in_front(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.assertIsNone(f.explorer.facing_npc())
        key(g, pygame.K_RETURN)
        self.assertIsNone(f.sub)

    def test_every_person_can_be_talked_to_and_platino_can_be_found(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        start = a.tile_at(f.explorer.walker.x, f.explorer.walker.y)
        world = f.game.save.world
        for person in a.people:                         # Round RPG16: every place of every person, at an hour they are there
            nid = person["id"]
            with self.subTest(nid=nid, tile=person["tile"]):
                hour = next(h for h in (12, 19, 23, 6) if conditions.all_hold(person["when"], world, minutes=clock.DAY + h * 60))
                world["clock"] = clock.DAY + hour * 60 + 1
                f.explorer.refresh(force=True)
                self.assertIsNotNone(talk_spot(a, nid, start, at=person["tile"]), nid)
                self.stand(g, nid)
                self.assertEqual(f.explorer.facing_npc(), nid)
                key(g, pygame.K_RETURN)
                self.assertEqual((f.sub, f.talk_box.name), ("talk", talk.npcs()[nid]["name"]))
                self.say_all(g, answer=False)
                self.assertIsNone(f.sub)
                if f.stage == "town":                   # Round RPG15b: a shopkeeper's words open the shop (expected change)
                    self.assertIn(nid, ("card_seller", "pack_seller"))
                    self.press(g, "leave_town")
        self.assertEqual(talk_spot(a, "platino", start), ((1, 27), "down"))      # the nook in the trees, south-west

    def test_the_innkeeper_is_talked_to_across_her_counter(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        col, row = a.npcs[talk.INNKEEPER]["tile"]
        f.explorer._use(a, explore.Walker(a, *rpg_route.middle(a, (col, row + 2))))
        f.explorer.walker.facing = "up"
        self.assertTrue(a.blocked(col, row + 1))                       # the counter between
        self.assertEqual(f.explorer.facing_npc(), talk.INNKEEPER)


class StoryTests(WindowBase):
    def test_every_quest_can_be_finished_through_the_window(self):
        g, launcher = self.setup_game()
        f = self.in_town(g)
        w = f.game.save.world
        self.stand(g, "elder", walk=True)
        key(g, pygame.K_RETURN)
        said = self.say_all(g, answer=True)
        self.assertIn(talk.quests()["the_dark_hall"]["offer"][0], said)
        self.assertEqual(talk.quest_state(w, "the_dark_hall"), "active")
        self.stand(g, "lamplighter", walk=True)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "active")
        self.assertEqual(r8.on_disk(self.folder)["world"]["quests"]["lamplighters_debt"]["state"], "active")   # written
        for i in range(3):                                              # three Ember Bandits, through the table
            f = self.win(g, encounters.RandomFoe("ember_bandit"))
            self.assertEqual(f.sub, "result")
            key(g, pygame.K_RETURN)
        self.assertEqual(talk.quest_state(f.game.save.world, "lamplighters_debt"), "ready")
        f = self.on_map(g, "warrens_2")                                 # the Grave Priest
        boss = self.boss(f)
        f = self.win(g, boss)
        self.assertEqual((f.sub, f.result[0]), ("result", "Victory"))
        key(g, pygame.K_RETURN)                                         # then his last words
        self.assertEqual((f.sub, f.talk_box.name), ("talk", "Grave Priest"))
        self.assertEqual(self.say_all(g), encounters.kind_text("grave_priest")["defeated"])
        w = f.game.save.world
        self.assertIn(talk.BOSS_FLAG, talk.flags(w))
        self.assertEqual(talk.quest_state(w, "the_dark_hall"), "ready")
        f = self.in_town(g)
        gold = f.game.save.gold
        self.stand(g, "elder")
        key(g, pygame.K_RETURN)
        said = self.say_all(g)
        q = talk.quests()["the_dark_hall"]
        self.assertEqual(said, q["ready"] + ["+50 gold."] + q["done"])
        self.assertEqual(f.game.save.gold, gold + 50)
        cards = f.game.save.collection.total()
        self.stand(g, "lamplighter")
        key(g, pygame.K_RETURN)
        said = self.say_all(g)
        self.assertTrue(any(p.startswith("New card: ") for p in said), said)
        self.assertEqual(f.game.save.collection.total(), cards + 1)
        disk = r8.on_disk(self.folder)["world"]
        self.assertEqual({k: v["state"] for k, v in disk["quests"].items()}, {"the_dark_hall": "done", "lamplighters_debt": "done"})
        self.assertIn("has_orrins_map", disk["flags"])
        key(g, pygame.K_ESCAPE)
        info = [t for t, _c in f.menu.info]
        self.assertIn("The Dark Hall - Finished:", info)
        self.assertIn("The Lamplighter's Debt - Finished:", info)

    def test_the_boss_speaks_before_the_way_in(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_2")
        boss = self.boss(f)
        cues = []
        g.play_cue = lambda cid, *a, **k: cues.append(cid)
        with mock.patch.object(f.explorer, "step", return_value=("encounter", boss)):
            f.walk_tick(g, 1.0, pressed=(1, 0))
            f.walk_tick(g, 1.1, pressed=(1, 0))
        self.assertEqual((f.sub, f.talk_box.name), ("talk", "Grave Priest"))
        self.assertNotIn(renc.STING, cues)
        self.assertEqual(self.say_all(g), encounters.kind_text("grave_priest")["intro"])
        self.assertEqual(f.sub, "fight")
        self.assertIs(f.fight, boss)
        self.assertIn(renc.STING, cues)
        frame(g)
        self.assertIsInstance(f.encounter, renc.EncounterScreen)

    def test_the_inn_sets_the_return_point_and_a_lost_duel_wakes_you_there(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        said = self.say_all(g, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
        self.assertEqual(said[-2:], talk.inn()["rest"])
        self.assertEqual(r8.on_disk(self.folder)["world"]["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})
        f = self.on_map(g, "ashfen_outskirts", "from_town")
        gold = f.game.save.gold
        self.meet(g, encounters.RandomFoe("thornback"))
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=False)
        g.end_continue()
        frame(g)
        f = g.modal
        self.assertEqual((f.stage, f.sub, f.result[0]), ("map", "result", "Defeat"))
        self.assertEqual(f.explorer.area.name, TOWN)
        here = f.explorer.area.tile_at(f.explorer.walker.x, f.explorer.walker.y)
        self.assertEqual(here, f.explorer.area.tile_at(*f.explorer.area.spawn(explore.INN_SPAWN)))
        lost = gold - f.game.save.gold
        self.assertEqual(f.result[1][-1], f"You lost {lost} gold and woke at the inn.")
        key(g, pygame.K_RETURN)
        self.assertEqual((f.sub, f.talk_box.name), ("talk", talk.npcs()[talk.INNKEEPER]["name"]))
        self.assertEqual(self.say_all(g), talk.inn()["woke"])

    def test_the_hall_is_dark_until_the_boss_falls_then_the_keeper_offers_rematches(self):
        g, launcher = self.setup_game()
        f = self.in_town(g)
        view = f.views[TOWN]
        self.assertTrue(view.dark_tiles)
        a = f.explorer.area
        (c0, r0, c1, r1), _flag = a.dark_zones[0]
        hall = a.npcs[talk.HALL_KEEPER]["tile"]
        self.assertTrue(c0 <= hall[0] <= c1 and r0 <= hall[1] <= r1)
        dark_px = self.hall_brightness(g, f, hall)
        talk.set_flag(f.game.save.world, talk.BOSS_FLAG)
        f.game.save.record.setdefault("defeated", {})["ember_bandit"] = 1
        frame(g)
        self.assertFalse(view.dark_tiles)
        self.assertGreater(self.hall_brightness(g, f, hall), dark_px + 20)
        self.stand(g, talk.HALL_KEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)                                    # "A rematch"
        self.assertEqual(f.sub, "fight")
        self.assertIsInstance(f.fight, encounters.RematchFoe)
        self.assertEqual(f.fight.enemy, "ember_bandit")
        enc = self.meet(g, f.fight)
        self.press(g, "fight")
        self.assertEqual(launcher.calls[-1]["names"], ["Ember Bandit"])

    def hall_brightness(self, g, f, tile):
        frame(g)
        x, y = f.camera.to_screen(*rpg_route.middle(f.explorer.area, tile))
        if not g.screen.get_rect().collidepoint((x, y)):
            f.explorer._use(f.explorer.area, explore.Walker(f.explorer.area, *rpg_route.middle(f.explorer.area, (tile[0], tile[1] - 1))))
            frame(g)
            x, y = f.camera.to_screen(*rpg_route.middle(f.explorer.area, tile))
        zs = f.camera.zoom
        px = [g.screen.get_at((int(x) + dx, int(y) + dy)) for dx in (-6 * zs, 6 * zs) for dy in (-6 * zs, 6 * zs)]
        return sum(sum(p[:3]) for p in px) / (3 * len(px))


if __name__ == "__main__":
    unittest.main()
