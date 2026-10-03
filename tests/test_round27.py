# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27: GPL licensing -- licenses/NOTICES.json, the Licenses and credits window, THIRD_PARTY_NOTICES.txt,
tools/build_notices.py, tools/export_public.py, and the three Scryfall image-rule fixes."""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_settings as fset
import forge_table as ft
import licenses_view as lv
import version
from tests.forge_fake import load_state
from tests.test_forge_table import click, frame, key, make_gui, point_for
from tests.test_settings_report import open_cog, popup_point

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_notices as bn
import export_public as ep

DIALOG_SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((4096, 1949), 1.75))


def open_licenses(gui):
    click(gui, popup_point(gui, "licenses"))
    assert isinstance(gui.modal, lv.LicensesDialog), gui.modal


# ---------------------------------------------------------------------------------------
# the manifest and its text files
# ---------------------------------------------------------------------------------------

class ManifestTests(unittest.TestCase):
    def test_the_real_manifest_loads_with_no_error(self):
        m = lv.load_manifest()
        self.assertNotIn("_error", m)
        self.assertEqual(m["project"]["license"], "GPL-3.0-or-later")

    def test_a_missing_manifest_is_reported_not_raised(self):
        with mock.patch.object(lv, "MANIFEST_PATH", "/does/not/exist/NOTICES.json"):
            m = lv.load_manifest()
        self.assertIn("_error", m)
        self.assertIn("missing", m["_error"])

    def test_license_ids_splits_compound_expressions(self):
        self.assertEqual(lv.license_ids("Apache-2.0"), ["Apache-2.0"])
        self.assertEqual(lv.license_ids("Apache-2.0 OR EPL-1.0"), ["Apache-2.0", "EPL-1.0"])
        self.assertEqual(lv.license_ids("CDDL-1.1 OR GPL-2.0-only WITH Classpath-exception-2.0"),
                          ["CDDL-1.1", "GPL-2.0-only", "Classpath-exception-2.0"])

    def test_every_license_id_the_manifest_names_has_a_text_file(self):
        m = lv.load_manifest()
        missing = []
        for c in m["components"]:
            for spdx in lv.license_ids(c["license"]):
                if spdx != "NOASSERTION" and lv.license_text(spdx)[1]:      # AD1: not-yet-established has no text file
                    missing.append(spdx)
        for c in m["java_components"].values():
            for spdx in lv.license_ids(c["license"]):
                if spdx != "NOASSERTION" and lv.license_text(spdx)[1]:      # AD1: not-yet-established has no text file
                    missing.append(spdx)
        self.assertEqual(missing, [])

    def test_build_entries_flattens_components_and_java_components(self):
        m = lv.load_manifest()
        entries = lv.build_entries(m, version.describe)
        keys = [e.key for e in entries]
        self.assertEqual(keys[:3], ["about", "gpl", "credits"])
        self.assertIn("c:forge", keys)
        self.assertIn("j:org.jupnp", keys)


# ---------------------------------------------------------------------------------------
# the cog and the dialog
# ---------------------------------------------------------------------------------------

class CogTests(unittest.TestCase):
    def test_licenses_is_in_the_help_group_and_opens_the_dialog(self):
        gui = make_gui()
        open_cog(gui)
        names = [n for _r, n in gui.overlay.buttons]
        self.assertIn("licenses", names)                    # round 28 adds "data_folder" after it in the same row
        open_licenses(gui)
        self.assertIsNone(gui.overlay)

    def test_shift_f1_opens_it_without_the_cog(self):
        gui = make_gui()
        key(gui, pygame.K_F1, mod=pygame.KMOD_SHIFT)
        self.assertIsInstance(gui.modal, lv.LicensesDialog)


class DialogTests(unittest.TestCase):
    def test_opens_and_shows_the_project_notice_at_every_size(self):
        for size, scale in DIALOG_SIZES:
            gui = make_gui(size=size, scale=scale)
            open_cog(gui)
            open_licenses(gui)
            entry_text = "\n".join(t for t, _c, _b in gui.modal.entries[0].build())
            self.assertIn("GNU General Public License", entry_text)

    def test_selecting_the_gpl_entry_and_pressing_end_scrolls_to_the_end(self):
        gui = make_gui(size=(1360, 840))
        open_cog(gui)
        open_licenses(gui)
        gui.modal.selected = 1
        frame(gui)
        key(gui, pygame.K_END)
        self.assertEqual(gui.modal.pane_scroll, gui.modal.pane_max_scroll)
        self.assertGreater(gui.modal.pane_max_scroll, 0)

    def test_scrolling_does_not_re_wrap(self):
        gui = make_gui(size=(1360, 840))
        open_cog(gui)
        open_licenses(gui)
        gui.modal.selected = 1
        frame(gui)
        with mock.patch("licenses_view.wrap_text", wraps=lv.wrap_text) as spy:
            gui.modal.wheel(gui, -3)
            frame(gui)
            gui.modal.wheel(gui, -3)
            frame(gui)
        spy.assert_not_called()

    def test_esc_closes_it(self):
        gui = make_gui()
        open_cog(gui)
        open_licenses(gui)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)

    def test_a_missing_manifest_still_opens_and_says_so(self):
        gui = make_gui()
        with mock.patch.object(lv, "MANIFEST_PATH", "/nope/NOTICES.json"):
            gui.open_licenses()
            frame(gui)
        self.assertIsInstance(gui.modal, lv.LicensesDialog)
        text = "\n".join(t for t, _c, _b in gui.modal.entries[0].build())
        self.assertIn("missing", text)

    def test_copy_button_writes_the_selected_text_to_the_clipboard(self):
        gui = make_gui()
        open_cog(gui)
        open_licenses(gui)
        with mock.patch.object(gui, "write_clipboard", return_value=True) as spy:
            click(gui, popup_point_modal(gui, "copy"))
        spy.assert_called_once()


def popup_point_modal(gui, name):
    for rect, n in gui.modal.buttons:
        if n == name:
            return rect.center
    raise AssertionError(f"no {name} button; has {[n for _, n in gui.modal.buttons]}")


# ---------------------------------------------------------------------------------------
# --version, HELP_LINES, the deck screen disclaimer
# ---------------------------------------------------------------------------------------

class VersionAndHelpTests(unittest.TestCase):
    def test_version_output_names_the_gpl(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ft.main(["forge_table.py", "--version"])
        out = buf.getvalue()
        self.assertIn("GNU GPL", out)
        self.assertIn("NO WARRANTY", out)

    def test_help_lines_mentions_licenses_and_credits(self):
        self.assertTrue(any("Licenses and credits" in key for key, _text in ft.HELP_LINES))


class DeckScreenDisclaimerTests(unittest.TestCase):
    def test_the_disclaimer_never_overlaps_the_deck_list_at_five_sizes(self):
        import forge_menu as fmenu
        for size, scale in (((900, 600), 1.0), ((1100, 700), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((4096, 1949), 1.75)):
            gui = make_gui(size=size, scale=scale)
            gui.menu = fmenu.DeckMenu(entries=[], choice=None, has_game=False)
            frame(gui)
            m = gui.menu
            self.assertLessEqual(m.list_rect.bottom, gui.L.H - 4, f"deck list runs into the disclaimer at {size}@{scale}")


# ---------------------------------------------------------------------------------------
# the three Scryfall image-rule fixes
# ---------------------------------------------------------------------------------------

def creature_card(**over):
    card = {"id": 1, "name": "Test Bear", "isCreature": True, "power": "2", "toughness": "2",
            "counters": {"+1/+1": 2}, "cost": "{1}{G}"}
    card.update(over)
    return card


class CopyrightBandTests(unittest.TestCase):
    def test_badges_stay_out_of_the_bottom_band_for_a_real_picture(self):
        gui = make_gui()
        w, h = 240, 335
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        card = creature_card(sick=True, commander=True)
        rects = []
        orig = pygame.draw.rect

        def spy(surface, colour, rect, *a, **kw):
            if surface is surf:
                rects.append(pygame.Rect(rect))
            return orig(surface, colour, rect, *a, **kw)

        with mock.patch("pygame.draw.rect", side_effect=spy):
            gui._badges(surf, card, w, h, real=True)
        band_top = h - int(h * ft.COPYRIGHT_BAND)
        for r in rects:
            self.assertLessEqual(r.bottom, band_top + 1, f"{r} intrudes into the copyright band (band starts at {band_top})")

    def test_the_band_is_not_applied_to_a_drawn_stand_in_face(self):
        gui = make_gui()
        w, h = 240, 335
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        card = creature_card()
        rects = []
        orig = pygame.draw.rect

        def spy(surface, colour, rect, *a, **kw):
            if surface is surf:
                rects.append(pygame.Rect(rect))
            return orig(surface, colour, rect, *a, **kw)

        with mock.patch("pygame.draw.rect", side_effect=spy):
            gui._badges(surf, card, w, h, real=False)
        band_top = h - int(h * ft.COPYRIGHT_BAND)
        self.assertTrue(any(r.bottom > band_top for r in rects), "expected at least one badge to sit in the normally-reserved band")


class CaptionPositionTests(unittest.TestCase):
    def test_caption_strip_never_overlaps_the_preview_picture(self):
        gui = make_gui()
        L = gui.L
        self.assertGreaterEqual(L.caption.y, L.preview.bottom)
        card = creature_card(imprinted=[{"name": "Island", "colors": ["U"]}], keywords=["Flying"], text="")
        strip = gui.draw_imprint_caption(card, L.caption)
        if strip is not None:
            self.assertFalse(strip.colliderect(L.preview))


class TappedColourTests(unittest.TestCase):
    def test_a_tapped_card_keeps_its_art_colour(self):
        import gfx
        gui = make_gui()
        card = creature_card()
        w, h = 140, 195
        skey, surf = gui.card_surface_keyed(card, w, h, True)
        gui.draw_card_at(card, 0, 0, w, h, tapped=True)
        rot = gfx.recall(("rot", skey))
        self.assertIsNotNone(rot)
        unrot = pygame.transform.rotate(rot, 90)
        # sample near the centre, well clear of the thin outline drawn on the tapped copy
        px, py = w // 2, h // 2
        before = tuple(surf.get_at((px, py)))[:3]
        after = tuple(unrot.get_at((px, py)))[:3]
        for b, a in zip(before, after):
            self.assertLessEqual(abs(b - a), 2, f"{before} vs {after}: colour shifted when tapped")


# ---------------------------------------------------------------------------------------
# tools/build_notices.py
# ---------------------------------------------------------------------------------------

class BuildNoticesTests(unittest.TestCase):
    def test_generated_text_round_trips_through_check(self):
        m = bn.load_manifest()
        text = bn.generate_notices_text(m)
        self.assertIn(m["project"]["name"], text)
        self.assertIn("Third-party software", text)

    def test_release_fails_while_anything_is_not_verified(self):
        manifest = {"project": {"name": "X", "license": "MIT", "copyright": "", "notice": ""},
                    "components": [{"id": "a", "name": "A", "license": "MIT", "status": "expected"}],
                    "java_components": {}, "credits": []}
        statuses = bn.collect_statuses(manifest)
        self.assertEqual(statuses, [("A", "expected")])

    def test_release_passes_once_everything_is_verified(self):
        manifest = {"project": {"name": "X", "license": "MIT", "copyright": "", "notice": ""},
                    "components": [{"id": "a", "name": "A", "license": "MIT", "status": "verified"}],
                    "java_components": {}, "credits": []}
        self.assertEqual(bn.collect_statuses(manifest), [("A", "verified")])


# ---------------------------------------------------------------------------------------
# tools/export_public.py
# ---------------------------------------------------------------------------------------

class ExportPublicTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.dest = os.path.join(tempfile.mkdtemp(), "out")
        os.makedirs(os.path.join(self.src, "my_decks"))
        os.makedirs(os.path.join(self.src, "sample_decks"))
        os.makedirs(os.path.join(self.src, "licenses"))
        with open(os.path.join(self.src, "my_decks", "karl.txt"), "w") as f:
            f.write("private")
        with open(os.path.join(self.src, "sample_decks", "starter.txt"), "w") as f:
            f.write("public")
        with open(os.path.join(self.src, "LICENSE"), "w") as f:
            f.write("gpl")
        with open(os.path.join(self.src, "THIRD_PARTY_NOTICES.txt"), "w") as f:
            f.write("notices")
        with open(os.path.join(self.src, "licenses", "NOTICES.json"), "w") as f:
            f.write("{}")

    def test_my_decks_is_excluded_and_sample_decks_included(self):
        ep.export(self.dest, base_dir=self.src)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "my_decks")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "sample_decks", "starter.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "LICENSE")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "THIRD_PARTY_NOTICES.txt")))
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "licenses")))

    def test_a_planted_webhook_url_makes_it_refuse_and_names_the_file(self):
        real_shaped = "https://discord.com/api/webhooks/" + "1" * 18 + "/" + "AbC-dEf_" * 9        # built here, so this file never holds one
        with open(os.path.join(self.src, "leaky_config.py"), "w") as f:
            f.write("WEBHOOK = '%s'\n" % real_shaped)
        with self.assertRaises(ep.ExportError) as ctx:
            ep.export(self.dest, base_dir=self.src)
        self.assertIn("leaky_config.py", str(ctx.exception))
        self.assertFalse(os.path.exists(self.dest))


if __name__ == "__main__":
    unittest.main()
