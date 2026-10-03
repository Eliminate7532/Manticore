# SPDX-License-Identifier: GPL-3.0-or-later
"""
Headless tests for table_gui.py: they draw the real window with SDL's dummy video
driver and drive it with simulated mouse clicks and key presses (no display, no network).
    python -m unittest discover -s tests -v
Set GUI_SHOTS=some_folder to also save a screenshot after key steps.
"""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from forge_store import ForgeStoreMixin

try:
    import pygame
    import table_gui as tg
    import game_actions as ga
    from game_state import GameState, Player
except ImportError as e:            # pygame not installed: skip instead of failing
    pygame = None
    _IMPORT_ERROR = e

with open(os.path.join(HERE, "fixtures", "kinnan_cards.json"), encoding="utf-8") as f:
    CARDS = json.load(f)

KINNAN = "Kinnan, Bonder Prodigy"
SHOTS = os.environ.get("GUI_SHOTS")


class FakeStore:
    """Card data from the saved fixture; no images and no network."""
    last_error = None

    def peek_card(self, name):
        key = re.sub(r"(?<!/) / (?!/)", " // ", name.strip().lower())
        return CARDS.get(key)

    def peek_image_path(self, name):
        return None

    def get_image_path(self, name):
        return None

    def prefetch_cards(self, names):
        return 0


class ForgeFakeStore(ForgeStoreMixin, FakeStore):
    """FakeStore plus the saved Forge scripts, so the rules come from Forge where a script exists."""


@unittest.skipIf(pygame is None, "pygame is not installed")
class GuiTests(unittest.TestCase):
    STORE_CLASS = FakeStore

    def setUp(self):
        self.make_gui()

    def make_gui(self, library=None, hand=None, mode="play", first_player=0):
        me = Player("Me", ["Forest"] * 30, commanders=[KINNAN])
        ai = Player("AI", ["Island"] * 30, commanders=[KINNAN])
        gs = GameState(me, ai)
        gs.start_game()
        if library is not None or mode == "play":   # fixed library order so tests don't depend on the shuffle
            me.library = list(library or (["Forest"] * 20 + ["Island"] * 10))
        gui = tg.TableGUI(gs, self.STORE_CLASS(), first_player=first_player)
        if hand is not None or mode == "play":      # a mulligan test keeps the seven cards it drew
            me.hand = list(hand if hand is not None else ["Forest", "Island", "Sol Ring"])
        if mode == "play":
            gui.begin_play()
        gui.render()
        self.gui = gui                              # the click/key helpers drive the newest window
        return gui

    # ---- helpers -----------------------------------------------------------------
    def shot(self, name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.gui.render()
            pygame.image.save(self.gui.screen, os.path.join(SHOTS, name + ".png"))

    def event(self, kind, **kw):
        self.gui.handle_event(pygame.event.Event(kind, **kw))
        self.gui.render()

    def click_pos(self, pos, button=1):
        self.event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)

    def click(self, kind, button=1, **match):
        hit = self.gui.hit_of_kind(kind, **match)
        self.assertIsNotNone(hit, f"no {kind} {match} on screen")
        self.click_pos(hit["rect"].center, button)

    def key(self, k):
        self.event(pygame.KEYDOWN, key=k)

    def menu_pick(self, label_start):
        menu = self.gui.menu
        self.assertIsNotNone(menu, "no menu is open")
        for rect, (label, cb) in zip(menu.item_rects, menu.items):
            if label.startswith(label_start):
                self.assertIsNotNone(cb, f"menu item '{label}' is greyed out")
                self.click_pos(rect.center)
                return
        self.fail(f"menu has no item starting with '{label_start}': {[l for l, _ in menu.items]}")

    def toast(self):
        return self.gui.toast[0]

    # ---- background loading ---------------------------------------------------------------
    def test_loader_runs_the_forge_download_and_reports_a_partial_result(self):
        class Loading(self.STORE_CLASS):
            calls = []

            class forge:
                last_error = "GitHub returned HTTP 503"

            def prefetch_forge(self, names):
                self.calls.append(sorted(names))
                return 3, 5
        me = Player("Me", ["Forest"] * 30, commanders=[KINNAN])
        gs = GameState(me, Player("AI", ["Island"] * 30))
        gs.start_game()
        store = Loading()
        gui = tg.TableGUI(gs, store, prefetch_names={"Sol Ring", "Forest"})
        self.assertTrue(gui.data_ready.wait(5), "the background loader never finished")
        self.assertEqual(store.calls, [["Forest", "Sol Ring"]])
        self.assertIn("3 of 5", gui.rules_note)
        self.assertIn("HTTP 503", gui.rules_note)
        gui.render()

    def test_loader_survives_a_forge_download_that_raises(self):
        class Broken(self.STORE_CLASS):
            def prefetch_forge(self, names):
                raise RuntimeError("boom")
        me = Player("Me", ["Forest"] * 30, commanders=[KINNAN])
        gs = GameState(me, Player("AI", ["Island"] * 30))
        gs.start_game()
        gui = tg.TableGUI(gs, Broken(), prefetch_names={"Sol Ring"})
        self.assertTrue(gui.data_ready.wait(5))
        gui.render()

    # ---- opening hand ---------------------------------------------------------------
    def test_first_mulligan_is_free_then_keep_starts_game(self):
        g = self.make_gui(mode="mulligan")
        self.assertEqual(g.mode, "mulligan")
        self.key(pygame.K_m)
        self.assertEqual((g.me.mulligans, len(g.me.hand), g.mode), (1, 7, "mulligan"))
        self.key(pygame.K_k)
        self.assertEqual(g.mode, "play")
        self.assertEqual(len(g.me.hand), 7)          # first mulligan is free: nothing to bottom
        self.assertEqual(g.gs.turn_number, 1)

    def test_second_mulligan_bottoms_one_card_by_clicking(self):
        g = self.make_gui(mode="mulligan")
        self.key(pygame.K_m)
        self.key(pygame.K_m)
        lib_before = len(g.me.library)
        self.key(pygame.K_k)
        self.assertEqual(g.mode, "bottom")
        self.key(pygame.K_RETURN)                    # nothing selected yet: refuses
        self.assertEqual(g.mode, "bottom")
        self.click("hand", idx=3)
        self.click("hand", idx=4)                    # only one may be selected
        self.assertEqual(g.bottom_sel, {3})
        self.key(pygame.K_RETURN)
        self.assertEqual((g.mode, len(g.me.hand), len(g.me.library)), ("play", 6, lib_before + 1))

    # ---- playing cards ---------------------------------------------------------------
    def test_click_hand_plays_land_once_per_turn(self):
        g = self.gui
        self.click("hand", idx=0)
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Forest"])
        self.assertEqual(g.me.lands_played_this_turn, 1)
        self.click("hand", idx=0)                    # Island: second land drop refused
        self.assertEqual(len(g.me.battlefield), 1)
        self.assertIn("already played a land", self.toast())
        self.assertEqual(g.toast[1], "fail")

    def test_cast_spell_auto_taps_a_land_and_undo_restores(self):
        g = self.gui
        g.me.battlefield.append({"name": "Forest", "face": 0, "tapped": False, "counters": {},
                                 "summoning_sick": False, "is_land": True})
        g.render()
        self.click("hand", idx=2)                    # Sol Ring costs {1}
        names = [e["name"] for e in g.me.battlefield]
        self.assertEqual(names, ["Forest", "Sol Ring"])
        self.assertTrue(g.me.battlefield[0]["tapped"])
        self.key(pygame.K_z)
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Forest"])
        self.assertFalse(g.me.battlefield[0]["tapped"])
        self.assertIn("Sol Ring", g.me.hand)

    def test_cannot_afford_shows_error_and_changes_nothing(self):
        g = self.gui
        self.click("hand", idx=2)
        self.assertEqual(g.toast[1], "fail")
        self.assertIn("Can't pay", self.toast())
        self.assertEqual(len(g.me.hand), 3)

    def test_tap_mana_rock_then_manual_mana_keys(self):
        g = self.gui
        g.me.battlefield.append({"name": "Sol Ring", "face": 0, "tapped": False, "counters": {},
                                 "summoning_sick": False, "is_land": False})
        g.render()
        self.click("perm", idx=0)
        self.assertEqual(g.me.mana_pool.text(), "{C}{C}")
        self.assertTrue(g.me.battlefield[0]["tapped"])
        self.key(pygame.K_1)
        self.assertEqual(g.me.mana_pool.text(), "{W}{C}{C}")
        self.click("perm", idx=0)                    # tapped permanent: click untaps it
        self.assertFalse(g.me.battlefield[0]["tapped"])
        self.key(pygame.K_0)
        self.assertEqual(g.me.mana_pool.total(), 0)

    def test_multi_option_land_opens_choice_menu(self):
        g = self.gui
        g.me.battlefield.append({"name": "Breeding Pool", "face": 0, "tapped": False, "counters": {},
                                 "summoning_sick": False, "is_land": True})
        g.render()
        self.click("perm", idx=0)
        self.assertIsNotNone(g.menu)
        self.menu_pick("{U}")
        self.assertEqual(g.me.mana_pool.text(), "{U}")

    def test_cast_commander_then_tax(self):
        g = self.gui
        for name in ("Forest", "Island"):
            g.me.battlefield.append({"name": name, "face": 0, "tapped": False, "counters": {},
                                     "summoning_sick": False, "is_land": True})
        g.render()
        self.click("cmd")
        self.assertEqual(g.me.command_zone, [])
        self.assertEqual(g.me.battlefield[-1]["name"], KINNAN)
        self.assertTrue(all(e["tapped"] for e in g.me.battlefield[:2]))
        idx = len(g.me.battlefield) - 1              # commander dies: right-click > Graveyard
        self.click("perm", button=3, idx=idx)
        self.menu_pick("Graveyard")
        self.assertEqual(g.me.command_zone, [KINNAN])
        self.assertEqual(g.me.graveyard, [])         # went back to the command zone instead
        for e in g.me.battlefield:
            e["tapped"] = False
        g.render()
        self.click("cmd")                            # now costs {2}{G}{U}: two lands can't pay
        self.assertIn("Can't pay {2}", self.toast())
        self.assertEqual(g.me.command_zone, [KINNAN])

    def test_fetchland_flow_pays_life_and_shuffles(self):
        g = self.make_gui(library=["Forest", "Breeding Pool", "Island", "Sol Ring", "Command Tower"])
        g.me.battlefield.append({"name": "Misty Rainforest", "face": 0, "tapped": False, "counters": {},
                                 "summoning_sick": False, "is_land": True})
        g.render()
        self.click("perm", idx=0)
        self.assertIsNotNone(g.viewer)
        self.assertEqual(g.me.life, 39)
        self.assertEqual(g.me.graveyard, ["Misty Rainforest"])
        shown = [name for _r, _i, name in g.viewer_rows]
        self.assertEqual(sorted(shown), ["Breeding Pool", "Forest", "Island"])   # Forest or Island types; not Sol Ring/Tower
        self.shot("fetch_viewer")
        self.click_pos(g.viewer_rows[0][0].center)
        self.menu_pick("Put onto the battlefield")
        self.assertIsNone(g.viewer)
        self.assertEqual(g.me.battlefield[-1]["name"], "Breeding Pool")
        self.assertEqual(len(g.me.library), 4)
        self.menu_pick("Don't pay")                  # a fetched shock land asks whether to pay 2 life
        self.assertEqual((g.me.life, g.me.battlefield[-1]["tapped"]), (39, True))
        self.key(pygame.K_z)                         # one Undo rolls back the crack AND the fetch
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Misty Rainforest"])
        self.assertEqual(g.me.life, 40)
        self.assertEqual(len(g.me.library), 5)

    def test_fetch_with_nothing_found_shuffles_on_escape(self):
        g = self.make_gui(library=["Sol Ring", "Sol Ring"])
        g.me.battlefield.append({"name": "Misty Rainforest", "face": 0, "tapped": False, "counters": {},
                                 "summoning_sick": False, "is_land": True})
        g.render()
        self.click("perm", idx=0)
        self.assertEqual(g.viewer_rows, [])
        self.key(pygame.K_ESCAPE)
        self.assertIsNone(g.viewer)
        self.assertTrue(any("shuffles" in line for line in g.gs.log))

    # ---- turns, zones, menus -----------------------------------------------------------
    def test_end_turn_untaps_draws_and_resets_land_drop(self):
        g = self.gui
        self.click("hand", idx=0)
        g.me.battlefield[0]["tapped"] = True
        hand_before, lib_before = len(g.me.hand), len(g.me.library)
        self.key(pygame.K_SPACE)
        self.assertEqual(g.gs.turn_number, 2)
        self.assertEqual(g.me.lands_played_this_turn, 0)
        self.assertFalse(g.me.battlefield[0]["tapped"])
        self.assertEqual((len(g.me.hand), len(g.me.library)), (hand_before + 1, lib_before - 1))
        self.assertEqual(len(g.ai.hand), 8)          # the AI drew for its turn too
        self.key(pygame.K_z)
        self.assertEqual(g.gs.turn_number, 1)

    def test_library_box_click_draws_and_d_key_draws(self):
        g = self.gui
        self.click("box", zone="library")
        self.assertEqual(len(g.me.hand), 4)
        self.key(pygame.K_d)
        self.assertEqual(len(g.me.hand), 5)

    def test_hand_menu_discard_and_graveyard_viewer_returns_card(self):
        g = self.gui
        self.click("hand", button=3, idx=2)
        self.assertIsNotNone(g.menu)
        self.menu_pick("Discard")
        self.assertEqual(g.me.graveyard, ["Sol Ring"])
        self.click("box", zone="graveyard")
        self.assertEqual(g.viewer["zone"], "graveyard")
        self.click_pos(g.viewer_rows[0][0].center)
        self.menu_pick("To hand")
        self.assertEqual(g.me.graveyard, [])
        self.assertIn("Sol Ring", g.me.hand)
        self.key(pygame.K_ESCAPE)
        self.assertIsNone(g.viewer)

    def test_library_search_shuffles_when_closed(self):
        g = self.gui
        self.key(pygame.K_l)
        self.assertEqual(g.viewer["mode"], "search")
        rows = [name for _r, _i, name in g.viewer_rows]
        self.assertEqual(rows, ["Forest", "Island"])                     # grouped, sorted
        self.click_pos(g.viewer_rows[1][0].center)
        self.menu_pick("To hand")
        self.assertIn("Island", g.me.hand)
        self.key(pygame.K_ESCAPE)
        self.assertTrue(any("shuffles" in line for line in g.gs.log))

    def test_life_buttons(self):
        g = self.gui
        self.click("life", delta=1)
        self.assertEqual(g.me.life, 41)
        self.click("life", button=3, delta=-1)
        self.assertEqual(g.me.life, 36)

    def test_commander_right_click_cast_free(self):
        g = self.gui
        self.click("cmd", button=3)
        self.menu_pick("Cast without paying")
        self.assertEqual(g.me.battlefield[-1]["name"], KINNAN)

    def test_hover_sets_preview_and_help_toggles(self):
        g = self.gui
        hit = g.hit_of_kind("hand", idx=2)
        self.event(pygame.MOUSEMOTION, pos=hit["rect"].center, rel=(0, 0), buttons=(0, 0, 0))
        g.render()
        self.assertEqual(g.preview_card, ("Sol Ring", 0))
        self.key(pygame.K_h)
        self.assertTrue(g.help_open)
        self.click_pos((5, 5))
        self.assertFalse(g.help_open)

    def test_modal_dfc_back_face_is_offered_in_hand_menu(self):
        g = self.make_gui(hand=["Sink into Stupor // Soporific Springs"])
        self.click("hand", button=3, idx=0)
        self.menu_pick("Play back face")
        self.assertEqual(g.me.battlefield[0]["face"], 1)
        self.assertTrue(g.me.battlefield[0]["is_land"])
        self.assertIn("Pay 3 life", g.menu.items[0][0])                  # shock-style land: you choose
        self.assertEqual((g.me.life, g.me.battlefield[0]["tapped"]), (40, True))
        self.menu_pick("Pay 3 life")
        self.assertFalse(g.me.battlefield[0]["tapped"])
        self.assertEqual(g.me.life, 37)
        self.shot("back_face")

    def test_crowded_board_stays_inside_the_board_area(self):
        g = self.gui
        for i in range(45):
            name = ["Sol Ring", "Forest", "Llanowar Elves", "Chrome Mox"][i % 4]
            land = name == "Forest"
            g.me.battlefield.append({"name": name, "face": 0, "tapped": i % 3 == 0, "counters": {},
                                     "summoning_sick": False, "is_land": land})
        g.me.hand = ["Forest"] * 11
        g.render()
        for h in g.hits:
            if h["kind"] in ("perm", "hand"):
                self.assertGreaterEqual(h["rect"].left, 0)
                self.assertLessEqual(h["rect"].right, g.L.board_w, h)
        self.shot("crowded")

    def test_ai_cards_render_and_hover(self):
        g = self.gui
        g.ai.battlefield.append({"name": "Sol Ring", "face": 0, "tapped": True, "counters": {},
                                 "summoning_sick": False, "is_land": False})
        g.render()
        hit = g.hit_of_kind("ai_perm", idx=0)
        self.event(pygame.MOUSEMOTION, pos=hit["rect"].center, rel=(0, 0), buttons=(0, 0, 0))
        g.render()
        self.assertEqual(g.preview_card, ("Sol Ring", 0))


    # ---- window size, fullscreen, text size ------------------------------------------
    def check_layout(self, g, W, H):
        """Everything clickable is on screen, buttons don't overlap, rows are stacked in order."""
        L = g.L
        self.assertEqual((L.W, L.H), (W, H))
        for h in g.hits:
            r = h["rect"]
            self.assertTrue(0 <= r.left and 0 <= r.top and r.right <= W and r.bottom <= H, (W, H, L.t, h["kind"], tuple(r)))
        btns = [h for h in g.hits if h["kind"] == "btn"]
        for i, a in enumerate(btns):
            for b in btns[i + 1:]:
                self.assertFalse(a["rect"].colliderect(b["rect"]), (W, H, L.t, a["name"], b["name"]))
        self.assertLessEqual(L.hand_y + L.me_h, L.buttons_y)
        self.assertLessEqual(L.my_row1_y + L.me_h, L.my_row2_y)
        self.assertLessEqual(L.my_row2_y + L.me_h, L.pool_y)
        self.assertLessEqual(L.ai_row2_y + L.sm_h, L.divider_y)
        self.assertEqual(L.status_y + L.status_h, H)
        self.assertGreaterEqual(L.me_h, 48)
        self.assertLessEqual(L.prev_rect.right, W)
        self.assertGreaterEqual(L.log_bottom - L.log_y, 30)

    def test_layout_fits_every_window_size_and_text_size(self):
        g = self.make_gui(hand=["Forest", "Island", "Sol Ring", "Forest", "Island", "Sol Ring", "Forest"])
        for W, H in ((1280, 800), (1920, 1080), (2560, 1440), (1024, 640), (800, 520), (1000, 1000), (1500, 700)):
            g.resize_window((W, H))
            for scale in tg.TEXT_STEPS:
                g.text_scale = scale
                g.render()
                self.check_layout(g, W, H)

    def test_cards_get_bigger_in_a_bigger_window(self):
        g = self.gui
        g.resize_window((1280, 800))
        g.render()
        small = g.L.me_h
        g.resize_window((1920, 1080))
        g.render()
        self.assertGreater(g.L.me_h, small * 1.25)

    def test_click_still_lands_after_resize(self):
        g = self.gui
        g.resize_window((1920, 1080))
        g.render()
        self.click("hand", idx=0)
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Forest"])

    def test_text_size_keys_buttons_and_limits(self):
        g = self.gui
        self.assertEqual(g.text_scale, 1.0)
        base_px = g.L.px["body"]
        self.key(pygame.K_EQUALS)
        self.assertEqual(g.text_scale, 1.25)
        self.assertGreater(g.L.px["body"], base_px)
        self.click("btn", name="text_up")
        self.assertEqual(g.text_scale, 1.5)
        self.key(pygame.K_MINUS)
        self.assertEqual(g.text_scale, 1.25)
        self.event(pygame.KEYDOWN, key=pygame.K_0, mod=pygame.KMOD_CTRL)
        self.assertEqual(g.text_scale, 1.0)
        self.assertEqual(g.me.mana_pool.total(), 0)          # plain 0 empties the pool; Ctrl+0 must not add anything
        self.key(pygame.K_MINUS)                             # already the smallest
        self.assertEqual(g.text_scale, 1.0)
        self.assertIn("minimum", self.toast())
        self.assertIsNone(g.hit_of_kind("btn", name="text_down"))    # A- is greyed out (not clickable) at 100%
        for _ in range(8):
            self.key(pygame.K_PLUS)
        self.assertEqual(g.text_scale, 2.0)
        self.assertIsNone(g.hit_of_kind("btn", name="text_up"))

    def test_bigger_text_still_plays_normally(self):
        g = self.gui
        g.change_text_scale(+1)
        g.change_text_scale(+1)
        g.render()
        self.click("hand", idx=0)
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Forest"])
        self.click("hand", button=3, idx=1)
        self.assertIsNotNone(g.menu)
        self.assertGreater(g.menu.font_px, 15)
        self.shot("text_150")

    def test_menu_closes_when_the_window_is_resized(self):
        g = self.gui
        self.click("hand", button=3, idx=0)
        self.assertIsNotNone(g.menu)
        g.resize_window((1500, 900))
        g.render()
        self.assertIsNone(g.menu)

    def test_fullscreen_toggle_by_key_button_and_escape(self):
        g = self.gui
        g.resize_window((1100, 700))
        g.render()
        self.key(pygame.K_F11)
        self.assertTrue(g.fullscreen)
        self.assertEqual(g.L.W, pygame.display.get_surface().get_width())
        self.check_layout(g, g.L.W, g.L.H)
        self.key(pygame.K_ESCAPE)                            # Esc leaves fullscreen
        self.assertFalse(g.fullscreen)
        self.assertEqual((g.L.W, g.L.H), (1100, 700))        # back to the previous window size
        self.click("btn", name="fullscreen")
        self.assertTrue(g.fullscreen)
        self.event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=pygame.KMOD_ALT)
        self.assertFalse(g.fullscreen)
        self.shot("windowed_again")

    def test_settings_are_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            me = Player("Me", ["Forest"] * 30, commanders=[KINNAN])
            gs = GameState(me, Player("AI", ["Island"] * 30))
            gui = tg.TableGUI(gs, FakeStore(), settings_path=path, window_size=(1300, 850))
            gui.render()
            gui.change_text_scale(+1)
            gui.change_text_scale(+1)
            gui.resize_window((1400, 900))
            gui.render()
            gui.save_settings()
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual((saved["text_scale"], saved["fullscreen"], saved["window_size"]), (1.5, False, [1400, 900]))
            again = tg.TableGUI(gs, FakeStore(), settings_path=path)
            again.render()
            self.assertEqual(again.text_scale, 1.5)
            self.assertEqual(again.screen.get_size(), (1400, 900))

    def test_broken_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ not json")
            gs = GameState(Player("Me", ["Forest"] * 30), Player("AI", ["Island"] * 30))
            gui = tg.TableGUI(gs, FakeStore(), settings_path=path)
            gui.render()
            self.assertEqual((gui.text_scale, gui.fullscreen), (1.0, False))

    def test_help_fits_on_screen_at_every_text_size(self):
        g = self.gui
        g.resize_window((1280, 800))
        for scale in tg.TEXT_STEPS:
            g.text_scale = scale
            g.help_open = True
            g.render()
        self.shot("help_200")
        self.click_pos((5, 5))
        self.assertFalse(g.help_open)


@unittest.skipIf(pygame is None, "pygame is not installed")
class GuiTestsWithForge(GuiTests):
    """Every GUI test again, with the rules read from Forge scripts instead of the card text."""
    STORE_CLASS = ForgeFakeStore

    # ---- fetchland prompt --------------------------------------------------------------
    def fetch_game(self, hand=("Wooded Foothills", "Forest"), library=None):
        return self.make_gui(hand=list(hand),
                             library=library or ["Forest", "Breeding Pool", "Island", "Sol Ring", "Tropical Island"])

    def test_playing_a_fetchland_asks_whether_to_crack_it(self):
        g = self.fetch_game()
        self.click("hand", idx=0)
        self.assertEqual(g.me.battlefield[-1]["name"], "Wooded Foothills")
        self.assertIsNotNone(g.menu, "no prompt after playing the fetchland")
        labels = [label for label, _cb in g.menu.items]
        self.assertTrue(labels[0].startswith("Crack it now: pay 1 life"), labels)
        self.assertIn("Mountain or Forest", labels[0])
        self.assertEqual(g.me.life, 40)                # nothing paid yet
        self.shot("fetch_prompt")

    def test_cracking_from_the_prompt_pays_life_and_lists_only_legal_lands(self):
        g = self.fetch_game()
        self.click("hand", idx=0)
        self.menu_pick("Crack it now")
        self.assertEqual(g.me.life, 39)
        self.assertEqual(g.me.graveyard, ["Wooded Foothills"])
        self.assertIn("Paid 1 life", self.toast())
        self.assertIsNotNone(g.viewer)
        shown = sorted(name for _r, _i, name in g.viewer_rows)
        self.assertEqual(shown, ["Breeding Pool", "Forest", "Tropical Island"])   # Mountain or Forest by type
        self.shot("fetch_choice")
        self.click_pos(g.viewer_rows[0][0].center)
        self.menu_pick("Put onto the battlefield")
        self.assertEqual(g.me.battlefield[-1]["name"], "Breeding Pool")
        self.assertIsNotNone(g.menu)                   # a fetched shock land asks whether to pay
        self.menu_pick("Pay 2 life")
        self.assertEqual((g.me.life, g.me.battlefield[-1]["tapped"]), (37, False))
        self.key(pygame.K_z)                           # Undo: back to just the played fetchland
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Wooded Foothills"])
        self.assertEqual(g.me.life, 40)

    def test_keeping_the_fetchland_leaves_it_to_crack_later(self):
        g = self.fetch_game()
        self.click("hand", idx=0)
        self.menu_pick("Keep it")
        self.assertEqual(g.me.life, 40)
        self.assertEqual([e["name"] for e in g.me.battlefield], ["Wooded Foothills"])
        self.assertFalse(g.me.battlefield[0]["tapped"])
        self.click("perm", idx=0)                      # clicking it later cracks it
        self.assertEqual(g.me.life, 39)
        self.assertIsNotNone(g.viewer)

    def test_no_prompt_for_a_normal_land_or_a_tapped_fetchland(self):
        g = self.fetch_game(hand=("Forest", "Wooded Foothills"))
        self.click("hand", idx=0)
        self.assertIsNone(g.menu)
        g.me.lands_played_this_turn = 0
        self.click("hand", idx=0, button=3)
        self.menu_pick("Play land tapped")
        self.assertIsNone(g.menu)

    def test_prompt_still_offered_when_nothing_matches(self):
        g = self.fetch_game(library=["Sol Ring", "Sol Ring"])
        self.click("hand", idx=0)
        labels = [label for label, _cb in g.menu.items]
        self.assertIn("no Mountain or Forest left", labels[0])

    def test_fetch_badge_shows_on_untapped_fetchlands_only(self):
        g = self.fetch_game()
        self.click("hand", idx=0)
        self.key(pygame.K_ESCAPE)
        g.menu = None
        e = g.me.battlefield[0]
        seen = []
        original = g.draw_badge
        g.draw_badge = lambda ent, rect: (seen.append(ent["name"]), original(ent, rect))
        g.render()
        self.assertEqual(seen, ["Wooded Foothills"])
        self.assertTrue(ga.fetch_spec(g.store, e))
        e["tapped"] = True
        self.assertTrue(ga.fetch_spec(g.store, e))     # still a fetchland, but the badge is only for untapped ones

    # ---- Forge-driven cards ----------------------------------------------------------------
    def test_shock_land_prompt_declining_leaves_it_tapped_and_blocks_end_turn_until_answered(self):
        g = self.make_gui(hand=["Breeding Pool", "Forest"])
        self.click("hand", idx=0)
        labels = [label for label, _cb in g.menu.items]
        self.assertEqual(labels, ["Pay 2 life: Breeding Pool enters untapped", "Don't pay: it enters tapped"])
        self.assertIn("(pay 2 life to keep it untapped)", self.toast())
        self.click_pos((5, 5))                                           # dismiss the menu without answering
        self.assertIsNone(g.menu)
        self.assertIn("awaiting", g.me.battlefield[0])
        self.key(pygame.K_SPACE)
        self.assertEqual(g.gs.turn_number, 1)                            # End turn refused
        self.assertIsNotNone(g.menu)                                     # and the question came back
        self.menu_pick("Don't pay")
        self.assertEqual((g.me.life, g.me.battlefield[0]["tapped"]), (40, True))
        self.key(pygame.K_SPACE)
        self.assertEqual(g.gs.turn_number, 2)

    def test_clicking_a_land_that_is_waiting_asks_again(self):
        g = self.make_gui(hand=["Breeding Pool"])
        self.click("hand", idx=0)
        self.click_pos((5, 5))
        self.click("perm", idx=0)
        self.menu_pick("Pay 2 life")
        self.assertEqual((g.me.life, g.me.battlefield[0]["tapped"]), (38, False))

    def test_chrome_mox_asks_for_an_imprint_and_then_taps_for_that_colour(self):
        g = self.make_gui(hand=["Chrome Mox", "Elvish Spirit Guide", "Sol Ring"])
        self.click("hand", idx=0)
        self.assertIsNotNone(g.menu)
        labels = [label for label, _cb in g.menu.items]
        self.assertEqual(labels[:2], ["Exile Elvish Spirit Guide", "Don't imprint anything"])
        self.menu_pick("Exile Elvish Spirit Guide")
        mox = g.me.battlefield[0]
        self.assertEqual(mox["imprinted"], ["Elvish Spirit Guide"])
        self.assertNotIn("awaiting", mox)
        self.assertEqual(g.me.exile, ["Elvish Spirit Guide"])
        mox["summoning_sick"] = False
        g.render()
        self.click("perm", idx=0)
        self.assertEqual(g.me.mana_pool.pool["G"], 1)

    def test_chrome_mox_without_a_colour_says_why(self):
        g = self.make_gui(hand=["Chrome Mox", "Elvish Spirit Guide"])
        self.click("hand", idx=0)
        self.menu_pick("Don't imprint")
        self.click("perm", idx=0)
        self.assertIn("can't make mana", self.toast())
        self.assertFalse(g.me.battlefield[0]["tapped"])

    def test_mox_diamond_asks_to_discard_a_land_or_goes_to_the_graveyard(self):
        g = self.make_gui(hand=["Mox Diamond", "Forest", "Sol Ring"])
        self.click("hand", idx=0, button=3)
        self.menu_pick("Cast without paying")
        labels = [label for label, _cb in g.menu.items]
        self.assertEqual(labels[0], "Discard Forest")
        self.menu_pick("Don't discard")
        self.assertEqual(g.me.graveyard, ["Mox Diamond"])
        self.assertEqual(g.me.battlefield, [])

    def test_elvish_spirit_guide_can_be_exiled_from_hand_for_mana(self):
        g = self.make_gui(hand=["Elvish Spirit Guide", "Sol Ring"])
        self.click("hand", idx=0, button=3)
        self.menu_pick("Use for mana")
        self.assertEqual(g.me.mana_pool.pool["G"], 1)
        self.assertEqual(g.me.exile, ["Elvish Spirit Guide"])
        self.assertEqual(g.me.hand, ["Sol Ring"])


if __name__ == "__main__":
    unittest.main()
