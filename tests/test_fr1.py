# SPDX-License-Identifier: GPL-3.0-or-later
"""Round FR1: "Suggest a feature", the second tab of the F8 window (claude/FORMATS_SKETCH_2026-10-02.md §7)."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import pygame

import forge_menu as fmenu
import forge_settings as fset
import reporting
from tests.test_forge_table import click, frame, key, make_gui, point_for
from tests.test_settings_report import SIZES, WEBHOOK, popup_point

IDEAS_HOOK = "https://discord.com/api/webhooks/987654321098765432/IdEaS_hook-1"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        reporting._last_send[0] = 0.0
        self.addCleanup(lambda: reporting._last_send.__setitem__(0, 0.0))

    def config(self, **values):
        with open(os.path.join(self.folder, reporting.CONFIG_NAME), "w", encoding="utf-8") as f:
            json.dump(values, f)

    def gui(self, state="main1_lands", **kw):
        gui = make_gui(state, **kw)
        gui.report_folder = self.folder
        return gui

    def deck_screen(self, **kw):
        gui = make_gui(None, **kw)
        gui.report_folder = self.folder
        gui.menu = fmenu.DeckMenu(entries=[], choice=None, has_game=False)
        frame(gui, 2)
        return gui

    def idea_form(self, gui=None, name="Friend A", idea="Let me pin a card so it stays big"):
        gui = gui or self.gui()
        gui.open_report(fset.ReportDialog.IDEA)
        frame(gui)
        form = gui.overlay
        form.fields[0].text, form.fields[1].text = name, idea
        frame(gui)
        return gui, form


# ---- reporting.py: the message, the file, the address ------------------------------------------------------------------------------

class IdeaTextTests(Base):
    INFO = {"name": "Karl", "idea": "Pin a card", "why": "To watch my opponent's commander", "area": "Table"}

    def test_the_discord_message_has_the_heading_the_area_and_both_fields(self):
        text = reporting.idea_summary(self.INFO, "2026-10-03 09:00")
        first = text.splitlines()[0]
        self.assertIn("Feature idea from Karl", first)
        self.assertIn(reporting.version.short(), first)
        self.assertIn("Area: Table", first)
        self.assertIn("**Idea:** Pin a card", text)
        self.assertIn("**Why:** To watch my opponent's commander", text)

    def test_no_area_and_no_why_are_left_out(self):
        text = reporting.idea_summary({"name": "", "idea": "Pin a card"})
        self.assertNotIn("Area:", text)
        self.assertNotIn("Why:", text)
        self.assertIn("Feature idea from someone", text)

    def test_the_message_never_passes_discords_cap(self):
        text = reporting.idea_summary(dict(self.INFO, idea="x" * 1500, why="y" * 1500))
        self.assertLessEqual(len(text), reporting.DISCORD_CAP)
        self.assertTrue(text.endswith("..."))

    def test_the_saved_file_holds_everything_and_no_windows_user_name(self):
        home = os.path.expanduser("~")
        info = dict(self.INFO, idea=f"Read decks from {home}{os.sep}Decks and C:\\Users\\Karl\\Desktop\\list.txt")
        path, picture = reporting.save_idea(info, folder=self.folder)
        self.assertIsNone(picture)
        self.assertEqual(os.path.dirname(path), os.path.join(self.folder, "bug_reports"))
        self.assertRegex(os.path.basename(path), r"^idea_\d{8}_\d{6}\.txt$")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for needle in ("Manticore feature idea", "Sent by:  Karl", "Area:     Table", "IDEA", "Read decks from ~",
                       "WHAT IT WOULD HELP WITH", "To watch my opponent's commander", reporting.version.VERSION):
            self.assertIn(needle, text)
        self.assertNotIn("Users\\Karl", text)
        if len(home) > 3:
            self.assertNotIn(home, text)

    def test_two_ideas_in_one_second_get_two_files(self):
        now = reporting.datetime.datetime(2026, 10, 3, 9, 0, 0)
        a, _ = reporting.save_idea(self.INFO, folder=self.folder, now=now)
        b, _ = reporting.save_idea(self.INFO, folder=self.folder, now=now)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isfile(a) and os.path.isfile(b))

    def test_the_picture_is_saved_beside_it_only_when_asked_for(self):
        shot = lambda width: (b"\x89PNG fake", "png")
        path, picture = reporting.save_idea(self.INFO, folder=self.folder, screenshot=shot)
        self.assertEqual(os.path.dirname(picture), os.path.dirname(path))
        self.assertTrue(picture.endswith("_screen.png"))
        with open(path, encoding="utf-8") as f:
            self.assertIn(os.path.basename(picture), f.read())

    def test_a_failed_picture_never_loses_the_idea(self):
        def broken(width):
            raise RuntimeError("no picture")
        path, picture = reporting.save_idea(self.INFO, folder=self.folder, screenshot=broken)
        self.assertIsNone(picture)
        self.assertTrue(os.path.isfile(path))


class IdeaAddressTests(Base):
    def test_ideas_webhook_is_used_when_valid(self):
        self.config(discord_webhook=WEBHOOK, ideas_webhook=IDEAS_HOOK)
        self.assertEqual(reporting.ideas_webhook_url(self.folder), IDEAS_HOOK)
        self.assertEqual(reporting.idea_config_status(self.folder)[0], IDEAS_HOOK)
        self.assertEqual(reporting.webhook_url(self.folder), WEBHOOK)            # bug reports keep their own

    def test_no_ideas_webhook_means_the_bug_reports_address(self):
        self.config(discord_webhook=WEBHOOK)
        self.assertEqual(reporting.ideas_webhook_url(self.folder), WEBHOOK)
        url, note = reporting.idea_config_status(self.folder)
        self.assertEqual(url, WEBHOOK)
        self.assertIn("your idea", note)

    def test_a_bad_ideas_webhook_means_the_bug_reports_address(self):
        for bad in ("not a hook", "https://example.com/api/webhooks/1/x", 42, ""):
            with self.subTest(bad=bad):
                self.config(discord_webhook=WEBHOOK, ideas_webhook=bad)
                self.assertEqual(reporting.ideas_webhook_url(self.folder), WEBHOOK)

    def test_nothing_set_up_saves_an_idea_file(self):
        url, note = reporting.idea_config_status(self.folder)
        self.assertIsNone(url)
        self.assertIn("saves an idea file", note)

    def test_the_bundled_fallback_order_is_unchanged(self):
        """With no explicit folder: the tester's own file, else the installer's bundled alpha webhook - for ideas as for bugs."""
        own_path = os.path.join(self.folder, "own.json")
        bundled_path = os.path.join(self.folder, "bundled.json")
        other = "https://discord.com/api/webhooks/111111111111111111/Other_hook"

        def write(path, data):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        with mock.patch.object(reporting, "config_path", lambda folder=None: own_path), \
                mock.patch.object(reporting, "bundled_config_path", lambda: bundled_path):
            write(bundled_path, {"discord_webhook": WEBHOOK})
            self.assertEqual(reporting.ideas_webhook_url(), WEBHOOK)                 # only the bundled one
            write(own_path, {"discord_webhook": other})
            self.assertEqual(reporting.ideas_webhook_url(), other)                   # the tester's own wins
            write(own_path, {"discord_webhook": "broken", "ideas_webhook": IDEAS_HOOK})
            self.assertEqual(reporting.webhook_url(), WEBHOOK)                        # bugs: the bundled one, as before
            self.assertEqual(reporting.ideas_webhook_url(), IDEAS_HOOK)              # ideas: the tester's own ideas address


class PostTests(Base):
    def test_an_idea_picture_is_posted_as_an_image_under_the_idea_name(self):
        seen = {}

        class Session:
            def post(self, url, data=None, files=None, json=None, timeout=None):
                seen.update(url=url, data=data, files=files, json=json)
                return mock.Mock(status_code=200, text="")
        pic = os.path.join(self.folder, "idea_1_screen.png")
        with open(pic, "wb") as f:
            f.write(b"png")
        ok, _kind, _msg = reporting.post_to_discord(WEBHOOK, "hello", pic, session=Session(), username=reporting.IDEA_BOT_NAME)
        self.assertTrue(ok)
        name, _fh, ctype = seen["files"]["files[0]"]
        self.assertEqual((name, ctype), ("idea_1_screen.png", "image/png"))
        payload = json.loads(seen["data"]["payload_json"])
        self.assertEqual(payload["username"], "Manticore idea")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_text_only_is_a_plain_json_post(self):
        seen = {}

        class Session:
            def post(self, url, data=None, files=None, json=None, timeout=None):
                seen.update(files=files, json=json)
                return mock.Mock(status_code=204, text="")
        ok, _k, _m = reporting.post_to_discord(WEBHOOK, "hello", None, session=Session(), username=reporting.IDEA_BOT_NAME)
        self.assertTrue(ok)
        self.assertIsNone(seen["files"])
        self.assertEqual(seen["json"]["username"], "Manticore idea")

    def test_a_bug_report_is_still_a_zip_under_the_old_name(self):
        seen = {}

        class Session:
            def post(self, url, data=None, files=None, json=None, timeout=None):
                seen.update(files=files, data=data)
                return mock.Mock(status_code=200, text="")
        z = os.path.join(self.folder, "bugreport_x.zip")
        with open(z, "wb") as f:
            f.write(b"PK")
        reporting.post_to_discord(WEBHOOK, "bug", z, session=Session())
        self.assertEqual(seen["files"]["files[0]"][2], "application/zip")
        self.assertEqual(json.loads(seen["data"]["payload_json"])["username"], reporting.BOT_NAME)


# ---- the window ----------------------------------------------------------------------------------------------------------------------

class TabTests(Base):
    def test_f8_at_the_table_opens_the_bug_tab_and_on_the_deck_screen_the_idea_tab(self):
        gui = self.gui()
        key(gui, pygame.K_F8)
        self.assertEqual(gui.overlay.tab, "bug")
        gui = self.deck_screen()
        key(gui, pygame.K_F8)
        self.assertIsInstance(gui.overlay, fset.ReportDialog)
        self.assertEqual(gui.overlay.tab, "idea")

    def test_f8_on_the_title_screen_opens_the_idea_tab(self):
        gui = make_gui(None)
        gui.report_folder = self.folder
        gui.open_boot(False)
        frame(gui, 2)
        key(gui, pygame.K_F8)
        self.assertIsInstance(gui.overlay, fset.ReportDialog)
        self.assertEqual(gui.overlay.tab, "idea")

    def test_the_cogs_bug_or_idea_button_opens_the_bug_tab(self):
        gui = self.gui()
        click(gui, point_for(gui, "button", name="settings"))
        click(gui, popup_point(gui, "report"))
        self.assertIsInstance(gui.overlay, fset.ReportDialog)
        self.assertEqual(gui.overlay.tab, "bug")

    def test_switching_tabs_keeps_what_was_typed_and_shares_the_name(self):
        gui = self.gui()
        key(gui, pygame.K_F8)
        form = gui.overlay
        form.fields[0].text, form.fields[1].text, form.fields[2].text = "Sam", "the mirror copied itself", "nothing"
        click(gui, popup_point(gui, "tab_idea"))
        self.assertEqual(form.tab, "idea")
        self.assertEqual(form.fields[0].text, "Sam")                     # one name box for both
        self.assertEqual(form.fields[1].text, "")
        form.fields[1].text = "a bigger stack"
        key(gui, pygame.K_TAB, mod=pygame.KMOD_CTRL)                      # Ctrl+Tab goes back
        self.assertEqual(form.tab, "bug")
        self.assertEqual([f.text for f in form.fields], ["Sam", "the mirror copied itself", "nothing"])
        click(gui, popup_point(gui, "tab_idea"))
        self.assertEqual(form.fields[1].text, "a bigger stack")
        self.assertEqual(form.focus, 1)                                  # a name is there: straight to the idea box

    def test_typing_goes_into_the_idea_box(self):
        gui, form = self.idea_form(idea="")
        form.focus = 1
        for ch in "zoom":
            key(gui, ord(ch), unicode=ch)
        self.assertEqual(form.fields[1].text, "zoom")
        self.assertEqual(form.tab_fields["bug"][1].text, "")

    def test_area_chips_pick_one_and_a_second_click_clears_it(self):
        gui, form = self.idea_form()
        chips = dict((a, r) for r, a in form.chips)
        self.assertEqual(list(chips), list(reporting.IDEA_AREAS))
        click(gui, chips["AI"].center)
        self.assertEqual(form.area, "AI")
        click(gui, dict((a, r) for r, a in form.chips)["Online"].center)
        self.assertEqual(form.area, "Online")
        click(gui, dict((a, r) for r, a in form.chips)["Online"].center)
        self.assertIsNone(form.area)

    def test_the_picture_box_toggles(self):
        gui, form = self.idea_form()
        self.assertFalse(form.picture)
        click(gui, form.check_rect.center)
        self.assertTrue(form.picture)
        click(gui, form.check_rect.center)
        self.assertFalse(form.picture)

    def test_the_idea_needs_a_few_words_first(self):
        gui, form = self.idea_form(idea="ok")
        self.assertNotIn("save", [n for _r, n in form.buttons])
        form.fields[1].text = "a deck builder"
        frame(gui)
        self.assertIn("save", [n for _r, n in form.buttons])

    def test_both_tabs_fit_the_window_at_every_size(self):
        for size, scale in SIZES + (((1366, 768), 1.0), ((1920, 1080), 2.0)):
            for tab in ("bug", "idea"):
                with self.subTest(size=size, scale=scale, tab=tab):
                    gui = self.gui(size=size, scale=scale)
                    gui.open_report(tab)
                    form = gui.overlay
                    form.fields[1].text = "text " * 80
                    form.picture = True
                    frame(gui)
                    window = gui.screen.get_rect()
                    self.assertTrue(window.contains(form.rect), form.rect)
                    for rect, name in form.buttons:
                        self.assertTrue(form.rect.contains(rect), (name, rect))
                    for f in form.fields:
                        self.assertTrue(form.rect.contains(f.rect), f.rect)
                    tabs = [r for r, n in form.buttons if n.startswith("tab_")]
                    self.assertEqual(len(tabs), 2)
                    self.assertTrue(all(t.bottom < form.fields[0].rect.y for t in tabs))
                    if tab == "idea":
                        self.assertTrue(form.rect.contains(form.check_rect), form.check_rect)
                        send = next(r for r, n in form.buttons if n in ("send", "save"))
                        self.assertLessEqual(form.check_rect.bottom, send.y)
                        for r, _a in form.chips:
                            self.assertLessEqual(r.bottom, send.y)
                    click(gui, popup_point(gui, "save"))
                    self.assertTrue(window.contains(gui.overlay.rect))

    def test_the_chips_show_in_an_ordinary_window(self):
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 1.5)):
            with self.subTest(size=size, scale=scale):
                gui, form = self.idea_form(self.gui(size=size, scale=scale))
                self.assertEqual(len(form.chips), len(reporting.IDEA_AREAS))


class SaveAndSendTests(Base):
    def setUp(self):
        super().setUp()
        self.posts = []
        self.reply = (True, "sent", "Sent! Your bug report has been submitted.")
        real = reporting.post_to_discord

        def fake(url, text, zip_path=None, **kw):
            self.posts.append((url, text, zip_path, kw.get("username", reporting.BOT_NAME)))
            return self.reply
        reporting.post_to_discord = fake
        self.addCleanup(lambda: setattr(reporting, "post_to_discord", real))

    def wait(self, gui, phase):
        end = time.time() + 5
        while gui.overlay.phase != phase and time.time() < end:
            frame(gui)
            time.sleep(0.01)
        self.assertEqual(gui.overlay.phase, phase, gui.overlay.message)

    def test_with_no_webhook_the_idea_is_saved_as_a_file(self):
        gui, form = self.idea_form()
        self.assertIn("save", [n for _r, n in form.buttons])
        click(gui, popup_point(gui, "save"))
        self.assertEqual(form.phase, "saved")
        self.assertTrue(form.path.endswith(".txt") and os.path.isfile(form.path))
        self.assertEqual(os.listdir(os.path.dirname(form.path)), [os.path.basename(form.path)])     # no zip, nothing else
        self.assertEqual(self.posts, [])
        frame(gui)

    def test_sending_an_idea_posts_text_only_under_the_idea_name(self):
        self.config(discord_webhook=WEBHOOK)
        gui, form = self.idea_form()
        click(gui, dict((a, r) for r, a in form.chips)["Table"].center)
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        url, text, attached, username = self.posts[0]
        self.assertEqual((url, attached, username), (WEBHOOK, None, "Manticore idea"))      # no zip, no picture
        self.assertIn("Feature idea from Friend A", text)
        self.assertIn("Area: Table", text)
        self.assertEqual(form.message, "Sent! Your idea has been submitted.")
        self.assertTrue(os.path.isfile(form.path))                          # the local copy, every time
        frame(gui)

    def test_a_ticked_picture_goes_with_it(self):
        self.config(discord_webhook=WEBHOOK)
        gui, form = self.idea_form()
        click(gui, form.check_rect.center)
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        attached = self.posts[0][2]
        self.assertTrue(attached and os.path.isfile(attached))
        self.assertRegex(os.path.basename(attached), r"^idea_.*_screen\.(jpg|png)$")
        frame(gui)

    def test_ideas_go_to_the_ideas_webhook_and_bugs_stay_where_they_were(self):
        self.config(discord_webhook=WEBHOOK, ideas_webhook=IDEAS_HOOK)
        gui, form = self.idea_form()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        self.assertEqual(self.posts[0][0], IDEAS_HOOK)
        reporting._last_send[0] = 0.0
        click(gui, popup_point(gui, "close"))
        key(gui, pygame.K_F8)
        gui.overlay.fields[1].text = "the mirror copied itself"
        frame(gui)
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        self.assertEqual(self.posts[1][0], WEBHOOK)
        self.assertEqual(self.posts[1][3], reporting.BOT_NAME)
        self.assertTrue(self.posts[1][2].endswith(".zip"))

    def test_only_an_ideas_webhook_still_sends_ideas(self):
        self.config(ideas_webhook=IDEAS_HOOK)
        gui, form = self.idea_form()
        self.assertIn("send", [n for _r, n in form.buttons])
        click(gui, popup_point(gui, "tab_bug"))
        form.fields[1].text = "the mirror copied itself"
        frame(gui)
        self.assertIn("save", [n for _r, n in form.buttons])               # bugs: not set up, a file

    def test_the_one_a_minute_limit_is_shared_both_ways(self):
        self.config(discord_webhook=WEBHOOK)
        for first, then in (("bug", "idea"), ("idea", "bug")):
            with self.subTest(first=first):
                reporting._last_send[0] = 0.0
                gui = self.gui()
                gui.open_report(first)
                gui.overlay.fields[0].text, gui.overlay.fields[1].text = "Sam", "something worth saying"
                frame(gui)
                click(gui, popup_point(gui, "send"))
                self.wait(gui, "sent")
                click(gui, popup_point(gui, "close"))
                gui.open_report(then)
                gui.overlay.fields[1].text = "something else to say"
                frame(gui)
                self.assertNotIn("send", [n for _r, n in gui.overlay.buttons])
                key(gui, pygame.K_RETURN, mod=pygame.KMOD_CTRL)
                self.assertEqual(gui.overlay.phase, "form")
        self.assertEqual(len(self.posts), 2)

    def test_a_failed_send_keeps_the_file_and_offers_try_again(self):
        self.config(discord_webhook=WEBHOOK)
        self.reply = (False, "offline", "Could not reach Discord (no internet connection?).")
        gui, form = self.idea_form()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "failed")
        self.assertTrue(os.path.isfile(form.path))
        self.assertIn("retry", [n for _r, n in form.buttons])
        self.reply = (True, "sent", "Sent! Your bug report has been submitted.")
        click(gui, popup_point(gui, "retry"))
        self.wait(gui, "sent")
        self.assertEqual(len(os.listdir(os.path.dirname(form.path))), 1)   # the retry re-used the same file


class WordsTests(Base):
    def test_the_cog_button_says_bug_or_idea_and_fits(self):
        for scale in (1.0, 2.0):
            with self.subTest(scale=scale):
                gui = self.gui(size=(1360, 840), scale=scale)
                click(gui, point_for(gui, "button", name="settings"))
                rect = dict((n, r) for r, n in gui.overlay.buttons)["report"]
                self.assertLessEqual(gui.font("small", True).size("Bug or idea   (F8)")[0], rect.w - 8)
                rows = {r.y for r, _n in gui.overlay.buttons}
                self.assertLessEqual(len(rows), 12)                             # the pop-up is still 12 rows

    def test_the_deck_screen_names_f8(self):
        for size, scale in (((1360, 840), 1.0), ((1360, 840), 2.0), ((900, 600), 1.0), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                drawn, real = [], fmenu.clip_text

                def spy(text, font, width):
                    out = real(text, font, width)
                    drawn.append(out)
                    return out
                with mock.patch.object(fmenu, "clip_text", spy):
                    self.deck_screen(size=size, scale=scale)
                self.assertTrue(any(t in fmenu.HINTS for t in drawn), drawn[:3])           # whole, never cut short

    def test_the_help_window_tells_about_ideas(self):
        import forge_table as ft
        text = dict(ft.HELP_LINES)["F8 / Bug or idea"]
        self.assertIn("Suggest a feature", text)


if __name__ == "__main__":
    unittest.main()
