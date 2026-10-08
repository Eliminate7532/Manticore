# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AD1: the theme foundation (Diablo 1 / Vermis look) - palette, fonts, table background, the Settings control, licences.

All offline. See claude/SONNET_SPEC_AD1_2026-09-27.md (in the Manticore project) for what each rule is and why."""
import ast
import colorsys
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import crashlog
import forge_dialogs as fdlg
import forge_menu as fmenu
import forge_settings as fset
import forge_table as ft
import gfx
import paths
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import click, frame, make_gui, point_for
from tests.test_round27b import deck_text
import deck_library as lib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_notices as bn  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setUpModule():
    pygame.init()
    pygame.display.set_mode((64, 64))


@contextlib.contextmanager
def fresh_fonts():
    """A clean font cache (and the 'noted once' memory), restored afterwards, so a test can point the fonts somewhere else."""
    saved = (dict(gfx._FONTS), dict(gfx._TWINS), set(gfx._FONT_NOTED), dict(gfx._OK_CACHE))
    gfx._FONTS.clear(); gfx._TWINS.clear(); gfx._FONT_NOTED.clear(); gfx._OK_CACHE.clear()
    try:
        yield
    finally:
        gfx._FONTS.clear(); gfx._FONTS.update(saved[0])
        gfx._TWINS.clear(); gfx._TWINS.update(saved[1])
        gfx._FONT_NOTED.clear(); gfx._FONT_NOTED.update(saved[2])
        gfx._OK_CACHE.clear(); gfx._OK_CACHE.update(saved[3])


class PaletteTests(unittest.TestCase):
    def test_the_palette_is_exactly_the_specified_umber_set(self):
        want = {"BG_TOP": (29, 18, 8), "BG_BOTTOM": (47, 33, 23), "PANEL": (42, 29, 21), "PANEL_MINE": (30, 42, 32),
                "PANEL_OPP": (52, 24, 22), "PANEL_EDGE": (86, 68, 52), "WHITE": (248, 242, 231), "TEXT": (233, 227, 216),
                "DIM": (159, 150, 139), "GOLD": (206, 164, 82), "GOLD_DARK": (128, 96, 40)}
        for name, value in want.items():
            self.assertEqual(getattr(gfx, name), value, name)

    def test_the_semantic_colours_did_not_move(self):
        self.assertEqual((gfx.YELLOW, gfx.CYAN, gfx.GREEN, gfx.RED, gfx.ORANGE, gfx.SHADOW),
                         ((255, 221, 0), (96, 200, 236), (98, 210, 130), (230, 90, 84), (240, 150, 70), (0, 0, 0)))
        self.assertEqual(gfx.FRAME_COLOURS["U"], (70, 118, 184))
        self.assertEqual(gfx.MANA_FILL["G"], (156, 204, 156))

    def test_my_panel_and_the_opponents_panel_differ_in_hue_by_more_than_60_degrees(self):
        def hue(c):
            return colorsys.rgb_to_hsv(*(x / 255 for x in c))[0] * 360
        d = abs(hue(gfx.PANEL_MINE) - hue(gfx.PANEL_OPP))
        d = min(d, 360 - d)
        self.assertGreater(d, 60)

    def test_the_card_back_is_no_longer_navy(self):
        surf = gfx.card_back(100, 140, 8)
        self.assertEqual(tuple(surf.get_at((50, 40)))[:3], (52, 22, 20))


GUARDED = ("forge_table.py", "forge_dialogs.py", "forge_menu.py", "forge_settings.py", "licenses_view.py", "forge_fx.py", "backup_banner.py",
           "flow_screens.py", "anim.py", "boot_screens.py",          # Round AD2b / AD2c / AD2
           "tour.py",                                                # Round UX1
           "online_screens.py",                                      # Round MP1
           "update_screens.py",                                      # Round 30
           "stats_view.py")                                          # patch 44
# Colour-looking tuples that are allowed to stay outside gfx.py: value -> why it is not a colour anyone sees.
ALLOWED_LITERALS = {
    (255, 120, 120): "BLEND_RGB_MULT tint",
    (60, 60, 60): "BLEND_RGB_ADD tint",
    (255, 255, 255): "lerp-to-white target",
    (255, 255, 255, 255): "alpha mask",
    (150, 150, 160): "BLEND_RGB_MULT shade",
}


class NoColourLiteralsOutsideGfx(unittest.TestCase):
    def literals(self, name):
        with open(os.path.join(BASE, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Tuple) and len(n.elts) in (3, 4) and all(
                    isinstance(e, ast.Constant) and type(e.value) is int and 0 <= e.value <= 255 for e in n.elts):
                out.append((n.lineno, tuple(e.value for e in n.elts)))
        return out

    def test_no_module_draws_with_a_raw_colour(self):
        for name in GUARDED:
            stray = [(line, v) for line, v in self.literals(name) if v not in ALLOWED_LITERALS]
            self.assertEqual(stray, [], f"{name} has colour literals; give each a named token in gfx.py")

    def test_every_gfx_token_the_modules_use_exists(self):
        for name in GUARDED:
            with open(os.path.join(BASE, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "gfx":
                    self.assertTrue(hasattr(gfx, n.attr), f"{name}:{n.lineno} uses gfx.{n.attr}, which does not exist")


class FontTests(unittest.TestCase):
    def test_body_is_alegreya_scaled_by_1_10(self):
        f = gfx.get_font(20)
        ref = pygame.font.Font(gfx.font_path("body"), round(20 * gfx.BODY_SCALE))
        self.assertEqual(gfx.BODY_SCALE, 1.10)
        self.assertEqual(f.size("Hamburgefonstiv"), ref.size("Hamburgefonstiv"))
        self.assertEqual(f.get_height(), ref.get_height())
        self.assertIn(b"Alegreya", self.family_bytes(gfx.font_path("body")))

    @staticmethod
    def family_bytes(path):
        with open(path, "rb") as f:
            return f.read(200000)

    def test_bold_loads_the_bold_file(self):
        bold = gfx.get_font(20, True)
        ref = pygame.font.Font(gfx.font_path("bold"), round(20 * gfx.BODY_SCALE))
        self.assertEqual(bold.size("Hamburgefonstiv"), ref.size("Hamburgefonstiv"))
        self.assertNotEqual(bold.size("Hamburgefonstiv"), gfx.get_font(20).size("Hamburgefonstiv"))

    def test_display_loads_display_ttf_and_ignores_bold(self):
        d = gfx.get_font(28, role="display")
        ref = pygame.font.Font(gfx.font_path("display"), 28)
        self.assertEqual(d.size("Settings"), ref.size("Settings"))
        self.assertEqual(gfx.get_font(28, True, "display").size("Settings"), ref.size("Settings"))

    def test_display_below_16_px_is_the_body_font(self):
        self.assertIs(gfx.get_font(15, role="display"), gfx.get_font(15))
        self.assertIsNot(gfx.get_font(16, role="display"), gfx.get_font(16))

    def test_fonts_are_cached_by_size_bold_and_role(self):
        self.assertIs(gfx.get_font(21), gfx.get_font(21))
        self.assertIsNot(gfx.get_font(21), gfx.get_font(21, True))
        self.assertIsNot(gfx.get_font(21), gfx.get_font(21, role="display"))

    def test_display_ok(self):
        self.assertTrue(gfx.display_ok("Settings"))
        self.assertFalse(gfx.display_ok("40"))                                    # K3: a digit never uses the display face
        self.assertFalse(gfx.display_ok("−2"))                               # U+2212 (planeswalker costs): a digit
        self.assertFalse(gfx.display_ok("\u4e2d"))                           # a character the display face has no glyph for
        self.assertTrue(gfx.display_ok("Resume last game"))                      # spaces never count as missing (Cinzel, 2026-10-01)
        self.assertTrue(gfx.display_ok("Choose an opponent…"))                    # AvQest has the ellipsis
        self.assertFalse(gfx.display_ok("+"))                                     # symbol-only labels stay in the body font (AvQest's
        self.assertFalse(gfx.display_ok("-"))                                     # "+" is a cross pattee, its "-" a small bar)
        self.assertFalse(gfx.display_ok("\u2190"))
        self.assertTrue(gfx.display_ok("Compact board cards"))

    def test_pick_font_gives_the_body_twin_for_numbers(self):
        d = gfx.get_font(28, True, "display")
        self.assertIs(gfx.pick_font(d, "Settings"), d)
        # round 32: a string with letters stays in the display font, which draws its digits in the body twin (K3, per run)
        self.assertIs(gfx.pick_font(d, "Life 40"), d)
        self.assertEqual([(t, f) for t, f in d.runs("Life 40")], [("Life ", d.face), ("40", gfx.get_font(28, True))])
        self.assertIs(gfx.pick_font(d, "40"), gfx.get_font(28, True))                # no letter at all: all body twin
        body = gfx.get_font(28)
        self.assertIs(gfx.pick_font(body, "Life 40"), body)                       # a body font is never swapped

    def test_draw_text_sets_a_number_in_the_body_face(self):
        surf = pygame.Surface((300, 60))
        d = gfx.get_font(28, role="display")
        r = gfx.draw_text(surf, "40", 5, 5, d, (255, 255, 255))
        self.assertEqual(r.size, gfx.get_font(28).render("40", True, (255, 255, 255)).get_size())

    def test_without_the_assets_folder_it_falls_back_to_the_system_font_and_notes_it_once(self):
        empty = tempfile.mkdtemp()
        with fresh_fonts(), mock.patch.object(paths, "assets_dir", lambda: empty):
            before = os.path.getsize(crashlog.log_path()) if os.path.isfile(crashlog.log_path()) else 0
            f = gfx.get_font(18)
            self.assertGreater(f.get_height(), 0)
            gfx.get_font(19)
            gfx.get_font(30, role="display")
            gfx.get_font(31, role="display")
            with open(crashlog.log_path(), encoding="utf-8", errors="replace") as fh:
                text = fh.read()[before:]
            self.assertEqual(text.count("font file not used"), 2)                 # once for the body file, once for the display file
            self.assertIn("Alegreya-Regular.ttf", text)
            self.assertIn("AvQest.ttf", text)

    def test_the_font_files_are_the_ones_the_spec_names(self):
        import hashlib
        want = {"display": "5929a5fd6c16df17069ed7cf57b6ff61bbe3c959d9ef59b30120a3845d98fee6",   # AvQest 1.3 (Karl's choice, 2026-10-01)
                "body": "30e4641d64a136214335d349fad808f4754f6ebeeed374a5c7751095d5c5f39c",
                "bold": "0e40dc361858a9112b42d2ac776ed60e63fcd60757b3a233843a74aafa334de7"}
        for which, digest in want.items():
            with open(gfx.font_path(which), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, which)

    def test_the_fallback_display_font_is_cinzel_decorative(self):
        import hashlib
        with open(os.path.join(BASE, "assets", "fonts", gfx.FALLBACK_FILES["display"]), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), "e854e68a388aa50d742a4415c1ae5c17a617ef7956c95a70021d0a4a44f20518")

    def test_without_avqest_the_display_face_is_cinzel_and_nothing_is_noted(self):
        import shutil
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "fonts"))
        for name in ("Alegreya-Regular.ttf", "Alegreya-Bold.ttf", "CinzelDecorative-Bold.ttf"):     # what the public source copy has
            shutil.copy(os.path.join(BASE, "assets", "fonts", name), os.path.join(d, "fonts", name))
        with fresh_fonts(), mock.patch.object(paths, "assets_dir", lambda: d):
            self.assertEqual(os.path.basename(gfx.font_path("display")), "CinzelDecorative-Bold.ttf")
            before = os.path.getsize(crashlog.log_path()) if os.path.isfile(crashlog.log_path()) else 0
            disp = gfx.get_font(32, role="display")
            ref = pygame.font.Font(os.path.join(d, "fonts", "CinzelDecorative-Bold.ttf"), 32)
            self.assertEqual(disp.size("Settings"), ref.size("Settings"))
            after = os.path.getsize(crashlog.log_path()) if os.path.isfile(crashlog.log_path()) else 0
            if after > before:
                with open(crashlog.log_path(), encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn("font file not used", fh.read()[before:])

    def test_the_public_export_leaves_avqest_out_and_keeps_cinzel(self):
        sys.path.insert(0, os.path.join(BASE, "tools"))
        import export_public as ep
        files = set(ep.iter_source_files(BASE))
        self.assertNotIn("assets/fonts/AvQest.ttf", files)
        self.assertNotIn("assets/fonts/1001fonts-avqest-eula.txt", files)
        self.assertIn("assets/fonts/CinzelDecorative-Bold.ttf", files)
        self.assertIn("assets/fonts/Alegreya-Regular.ttf", files)

    def test_the_table_maps_only_title_and_btn_to_the_display_face(self):
        gui = make_gui("main1_start")
        self.assertEqual(ft.DISPLAY_KEYS, {"title", "btn"})
        for key in ("body", "small", "tiny", "hint", "big"):
            self.assertIs(gui.font(key), gfx.get_font(gui.L.px[key], False, "body"), key)
        self.assertIs(gui.font("title", True), gfx.get_font(gui.L.px["title"], True, "display"))


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        gfx._BG_SLOT[0] = None

    def test_the_size_is_right_for_each_name(self):
        for name in gfx.BACKGROUND_FILES:
            for size in ((800, 500), (1360, 840)):
                self.assertEqual(gfx.table_background(size, name).get_size(), size, (name, size))

    def test_the_last_result_is_kept_and_a_new_size_or_name_rebuilds(self):
        a = gfx.table_background((640, 400), "cathedral")
        self.assertIs(a, gfx.table_background((640, 400), "cathedral"))
        b = gfx.table_background((641, 400), "cathedral")
        self.assertIsNot(a, b)
        self.assertEqual(b.get_size(), (641, 400))
        self.assertIsNot(b, gfx.table_background((641, 400), "ruins"))

    def test_plain_is_the_old_gradient(self):
        self.assertIs(gfx.table_background((500, 300), "plain"), gfx.gradient((500, 300), gfx.BG_TOP, gfx.BG_BOTTOM))

    def test_a_picture_is_darker_than_its_source_and_darkest_in_the_corners(self):
        surf = gfx.table_background((800, 500), "graveyard")
        raw = gfx._bg_raw("graveyard")

        def bright(s, p):
            return sum(tuple(s.get_at(p))[:3])
        self.assertLess(sum(bright(surf, (x, y)) for x, y in ((400, 250), (300, 200), (500, 300))),
                        sum(bright(raw, (x, y)) for x, y in ((584, 392), (450, 320), (700, 470))))
        corner = sum(bright(surf, p) for p in ((2, 2), (797, 2), (2, 497), (797, 497)))
        middle = sum(bright(surf, p) for p in ((400, 250), (380, 240), (420, 260), (400, 230)))
        self.assertLess(corner, middle)

    def test_a_missing_file_falls_back_to_plain(self):
        empty = tempfile.mkdtemp()
        saved = dict(gfx._BG_RAW), set(gfx._BG_NOTED)
        gfx._BG_RAW.clear(); gfx._BG_NOTED.clear()
        try:
            with mock.patch.object(paths, "assets_dir", lambda: empty):
                surf = gfx.table_background((420, 260), "citadel")
            self.assertIs(surf, gfx.gradient((420, 260), gfx.BG_TOP, gfx.BG_BOTTOM))
        finally:
            gfx._BG_RAW.clear(); gfx._BG_RAW.update(saved[0])
            gfx._BG_NOTED.clear(); gfx._BG_NOTED.update(saved[1])

    def test_an_unknown_name_is_plain_too(self):
        self.assertIs(gfx.table_background((420, 261), "alternate"), gfx.gradient((420, 261), gfx.BG_TOP, gfx.BG_BOTTOM))

    def test_the_four_pictures_and_their_hashes_are_the_delivered_ones(self):
        import hashlib
        want = {"graveyard.jpg": "15bd9a70535447186104c0ebeff8844512a493056dc0cb3c4f7180c0f3f23362",
                "cathedral.jpg": "a0e2e1c86e9c81f07f3271c7f91010489ee4691f40a192b4122c95051cce1c90",
                "citadel.png": "063c6d4caa3cb2890733d88a334c13dffbeb7ed0f53bd5fe140a6334336f837b",
                "ruins.jpg": "189afa7088eac1f0b2c38854d8ca9d5128afb9419045d039e74a2eff040d7f61"}
        for fname, digest in want.items():
            with open(os.path.join(paths.assets_dir(), "backgrounds", fname), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, fname)


class RotationTests(TempDecks):
    def setUp(self):
        super().setUp()
        for name, commander in (("Elves", "Lathril, Blade of the Elves"), ("Tokens", "Adeline, Resplendent Cathar")):
            lib.save_text(name, deck_text(commander), self.lib, self.tmp)
        self.settings = os.path.join(self.tmp, "settings.json")

    def saved(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def start(self, gui):
        entries = {e.name: e for e in lib.list_decks(*self.dirs)}
        self.assertIsNone(gui.start_game(entries["Elves"], entries["Tokens"], 1, {"count": 1}))

    def test_a_first_run_shows_the_graveyard_before_any_game(self):
        gui = self.idle_gui(settings=self.settings)
        self.assertEqual((gui.table_background, gui.current_bg), ("rotate", "graveyard"))

    def test_five_games_rotate_through_the_four_pictures_and_save_it(self):
        gui = self.idle_gui(settings=self.settings)
        seen = []
        for _ in range(5):
            self.start(gui)
            seen.append(gui.current_bg)
            self.assertEqual(self.saved()["table_background_last"], gui.current_bg)
        self.assertEqual(seen, ["graveyard", "cathedral", "citadel", "ruins", "graveyard"])
        self.assertEqual(self.saved()["table_background"], "rotate")

    def test_the_deck_screen_shows_the_last_one_used_and_the_next_game_moves_on(self):
        gui = self.idle_gui(settings=self.settings)
        self.start(gui)
        self.start(gui)
        again = self.idle_gui(settings=self.settings)                            # the program opened again
        self.assertEqual(again.current_bg, "cathedral")
        self.start(again)
        self.assertEqual(again.current_bg, "citadel")

    def test_restart_also_moves_on(self):
        gui = self.idle_gui(settings=self.settings)
        self.start(gui)
        gui.restart_game()
        self.assertEqual(gui.current_bg, "cathedral")

    def test_a_fixed_picture_stays_put(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"table_background": "cathedral"}, f)
        gui = self.idle_gui(settings=self.settings)
        self.assertEqual(gui.current_bg, "cathedral")
        for _ in range(3):
            self.start(gui)
            self.assertEqual(gui.current_bg, "cathedral")

    def test_an_old_settings_file_without_the_keys_is_rotate(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"text_scale": 1.25, "fullscreen": False}, f)
        gui = self.idle_gui(settings=self.settings)
        self.assertEqual((gui.table_background, gui.table_background_last), ("rotate", None))

    def test_an_unknown_value_including_alternate_is_rotate(self):
        for bad in ("alternate", "sunset", "", None, 7, ["graveyard"]):
            with open(self.settings, "w", encoding="utf-8") as f:
                json.dump({"table_background": bad, "table_background_last": bad}, f)
            gui = self.idle_gui(settings=self.settings)
            self.assertEqual((gui.table_background, gui.table_background_last), ("rotate", None), bad)


class SettingsControlTests(TempDecks):
    def popup_point(self, gui, name):
        for rect, n in gui.overlay.buttons:
            if n == name:
                return rect.center
        raise AssertionError(name)

    def open_cog(self, gui):
        click(gui, point_for(gui, "button", name="settings"))
        self.assertIsInstance(gui.overlay, fset.SettingsPopup)

    def test_the_buttons_cycle_the_setting_and_save_it(self):
        path = os.path.join(self.tmp, "settings.json")
        gui = make_gui("main1_start", settings=path)
        self.open_cog(gui)
        order = ["rotate", "graveyard", "cathedral", "citadel", "ruins", "plain"]
        self.assertEqual(gui.table_background, "rotate")
        for want in order[1:] + ["rotate"]:
            click(gui, self.popup_point(gui, "bg_next"))
            self.assertEqual(gui.table_background, want)
        click(gui, self.popup_point(gui, "bg_prev"))
        self.assertEqual(gui.table_background, "plain")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["table_background"], "plain")

    def test_changing_it_mid_game_changes_the_picture_at_once_but_rotate_waits(self):
        gui = make_gui("main1_start")
        self.open_cog(gui)
        click(gui, self.popup_point(gui, "bg_next"))                              # rotate -> graveyard: shown at once
        self.assertEqual(gui.current_bg, "graveyard")
        click(gui, self.popup_point(gui, "bg_next"))                              # -> cathedral
        self.assertEqual(gui.current_bg, "cathedral")
        for _ in range(3):                                                        # -> citadel, ruins, plain
            click(gui, self.popup_point(gui, "bg_next"))
        self.assertEqual((gui.table_background, gui.current_bg), ("plain", "plain"))
        click(gui, self.popup_point(gui, "bg_next"))                              # -> rotate: what is on screen stays until the next game
        self.assertEqual((gui.table_background, gui.current_bg), ("rotate", "plain"))

    def test_the_popup_still_has_twelve_rows_and_the_label_shows(self):
        gui = make_gui("main1_start")
        self.open_cog(gui)
        self.assertEqual(gui.background_label(), "Rotate")
        for name in ("bg_prev", "bg_next", "sound", "hover_tick"):
            self.popup_point(gui, name)
        sound = dict((n, r) for r, n in gui.overlay.buttons)
        self.assertEqual(sound["sound"].y, sound["hover_tick"].y)                 # Sound | Hover tick share a row


# Which labels did clip_text shorten BEFORE this round? Recorded from the pre-AD1 tree by tools that spy on clip_text while rendering
# the same screens (the Settings pop-up, the deck screen with 1 and 3 AI seats, and the action bar in eight game states) at 1360x840.
# The old run had only the sandbox's system font (Segoe UI is Windows-only), so treat this as "what the old layout already clipped".
OLD_CLIPPED = {("1.0", "Report a bug   (F8)"), ("2.0", "Report a bug   (F8)")}
SETTINGS_LABELS = ["Text size", "Table", "Full screen   (F11)", "Animations", "Compact: Off", "Sort hand: Off", "Log: Always", "Focus: Always",
                   "Sound", "Hover tick",
                   "Volume", "New game...", "Concede...", "Controls   (H)", "Bug or idea   (F8)", "Licenses", "My data folder",
                   "Tour of the table"]
STATES = ["main1_start", "main1_lands", "mulligan", "coin_toss", "declare_attackers", "declare_blockers", "paying_mana", "stack_one"]


class NoNewClipping(TempDecks):
    def test_no_settings_deck_screen_or_action_bar_label_newly_clips(self):
        calls, labels = [], set()
        real_clip, real_button = gfx.clip_text, ft.ForgeTable.draw_button

        def spy(text, font, width):
            result = real_clip(text, font, width)
            calls.append((text, result))
            return result

        def button(gui, rect, label, *a, **k):
            if label:
                labels.add(label)
            return real_button(gui, rect, label, *a, **k)

        clipped = set()
        with mock.patch.object(gfx, "clip_text", spy), mock.patch.object(ft, "clip_text", spy), \
                mock.patch.object(fset, "clip_text", spy), mock.patch.object(fmenu, "clip_text", spy), \
                mock.patch.object(fdlg, "clip_text", spy), mock.patch.object(ft.ForgeTable, "draw_button", button):
            for scale in (1.0, 2.0):
                calls.clear()
                labels.clear()
                for state in STATES:
                    gui = make_gui(state, (1360, 840), scale)
                    click(gui, point_for(gui, "button", name="settings"))
                for seats in (1, 3):
                    gui = self.idle_gui(size=(1360, 840), scale=scale)
                    for _ in range(seats - 1):
                        click(gui, self.button_point(gui, "plus"))
                    gui.menu.opps[0] = fmenu.RANDOM
                    frame(gui, 2)
                known = labels | set(SETTINGS_LABELS)
                self.assertTrue({"Start game", "Pass priority", "Same as mine"} <= known or {"Start game", "OK"} <= known, known)
                clipped |= {(str(scale), text) for text, result in calls if text in known and result != text}
        self.assertEqual(clipped - OLD_CLIPPED, set(), "these labels are cut short now and were not before")


class LicenceTests(unittest.TestCase):
    def manifest(self, status, licence="NOASSERTION"):
        return {"project": {"name": "X", "license": "MIT", "copyright": "", "notice": ""},
                "components": [{"id": "f", "name": "F", "license": licence, "status": status}], "java_components": {}, "credits": []}

    def test_noassertion_is_allowed_only_while_the_entry_is_not_verified(self):
        self.assertEqual(bn.validate_manifest(self.manifest("unverified")), [])
        self.assertEqual(bn.validate_manifest(self.manifest("expected")), [])
        problems = bn.validate_manifest(self.manifest("verified"))
        self.assertEqual(len(problems), 1)
        self.assertIn("f:", problems[0])

    def test_noassertion_needs_no_text_file(self):
        text = bn.generate_notices_text(self.manifest("unverified"))
        self.assertNotIn("MISSING", text)
        self.assertIn("Licence not established", text)

    def test_the_real_manifest_has_the_three_new_entries_and_the_ofl_text(self):
        m = bn.load_manifest()
        by_id = {c["id"]: c for c in m["components"]}
        self.assertEqual((by_id["font-display"]["license"], by_id["font-display"]["status"], by_id["font-display"]["group"]),
                         ("LicenseRef-1001fonts-FFC", "verified", "Fonts"))     # AvQest: Karl, 2026-10-02 (author lists it as freeware)
        self.assertEqual((by_id["font-display-fallback"]["license"], by_id["font-display-fallback"]["status"]),
                         ("OFL-1.1", "verified"))                               # Cinzel Decorative, the public source's face
        self.assertTrue(os.path.isfile(os.path.join(BASE, "licenses", "texts", "LicenseRef-1001fonts-FFC.txt")))
        self.assertEqual((by_id["font-alegreya"]["license"], by_id["font-alegreya"]["status"], by_id["font-alegreya"]["group"]),
                         ("OFL-1.1", "verified", "Fonts"))
        self.assertEqual(by_id["art-backgrounds"]["license"], "NOASSERTION")
        self.assertEqual(by_id["art-backgrounds"]["status"], "attribution")      # Karl, 2026-10-02: "Created with Grok" credited
        self.assertTrue(os.path.isfile(os.path.join(BASE, "licenses", "texts", "OFL-1.1.txt")))
        with open(os.path.join(BASE, "licenses", "notices", "font-alegreya.txt"), encoding="utf-8") as f:
            self.assertEqual(len(f.read().rstrip("\n").split("\n")), 3)
        self.assertEqual(bn.validate_manifest(m), [])
        with open(os.path.join(BASE, "THIRD_PARTY_NOTICES.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), bn.generate_notices_text(m))

    def test_release_refuses_and_names_the_unverified_entry(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = bn.main(["--check", "--release"])
        self.assertEqual(code, 1)
        self.assertNotIn("[font-display]", err.getvalue())                         # AvQest verified by Karl, 2026-10-02
        self.assertNotIn("font-display-fallback", err.getvalue())
        self.assertNotIn("art-backgrounds", err.getvalue())                       # attribution given: release accepts it
        self.assertNotIn("font-alegreya", err.getvalue())

    def test_the_licences_dialog_lists_a_fonts_group_with_the_ofl_text(self):
        import licenses_view as lv
        entries = lv.build_entries(bn.load_manifest(), lambda: [])
        fonts = [e for e in entries if e.group == "Fonts"]
        self.assertEqual({e.label.split(" ")[0] for e in fonts}, {"AvQest", "Cinzel", "Alegreya", "Mana"})     # Mana: Round MANA1
        alegreya = [e for e in fonts if e.label == "Alegreya"][0]
        text = " ".join(t for t, _c, _b in alegreya.build())
        self.assertIn("SIL Open Font License", text)
        self.assertNotIn("is missing", text)
        display = " ".join(t for t, _c, _b in [e for e in fonts if e.label.startswith("Cinzel")][0].build())
        self.assertIn("SIL Open Font License", display)
        self.assertNotIn("is missing", display)
        avq = " ".join(t for t, _c, _b in [e for e in fonts if e.label.startswith("AvQest")][0].build())
        self.assertIn("1001Fonts Free For Commercial Use License", avq)
        self.assertNotIn("is missing", avq)
        art = [e for e in entries if e.key == "c:art-backgrounds"][0]
        self.assertEqual(art.group, "Artwork")
        self.assertNotIn("is missing", " ".join(t for t, _c, _b in art.build()))


if __name__ == "__main__":
    unittest.main()
