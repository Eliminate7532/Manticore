# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI tests for the settings cog (which replaced the row of buttons at the top right) and the bug report form."""
import io
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

import pygame

import forge_client as fc
import forge_dialogs as dlg
import forge_settings as fset
import forge_table as ft
import reporting
from tests.forge_fake import load_state
from tests.test_forge_table import SearchDialogTests, click, frame, key, make_gui, move, point_for

WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123"
SIZES = (((1360, 840), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.5))


def popup_point(gui, name):
    for rect, n in gui.overlay.buttons:
        if n == name:
            return rect.center
    raise AssertionError(f"no {name} button in the pop-up; it has {[n for _, n in gui.overlay.buttons]}")


def open_cog(gui):
    click(gui, point_for(gui, "button", name="settings"))
    assert isinstance(gui.overlay, fset.SettingsPopup)


def type_text(gui, text):
    for ch in text:
        if ch == "\n":
            key(gui, pygame.K_RETURN)
        else:
            key(gui, {"+": pygame.K_PLUS, "-": pygame.K_MINUS}.get(ch, ord(ch.lower()) if ch.isalnum() else pygame.K_SPACE), unicode=ch)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        reporting._last_send[0] = 0.0
        self.addCleanup(lambda: reporting._last_send.__setitem__(0, 0.0))

    def gui(self, state="main1_start", **kw):
        gui = make_gui(state, **kw)
        gui.report_folder = self.folder
        return gui

    def set_webhook(self, url=WEBHOOK):
        with open(os.path.join(self.folder, reporting.CONFIG_NAME), "w", encoding="utf-8") as f:
            json.dump({"discord_webhook": url}, f)


class CogTests(Base):
    def test_the_five_old_buttons_are_gone_and_one_cog_replaces_them(self):
        gui = self.gui()
        names = {d.get("name") for _r, k, d in gui.hits if k == "button"}
        self.assertIn("settings", names)
        for old in ("full", "bigger", "smaller", "newgame", "help"):
            self.assertNotIn(old, names)

    def test_the_cog_is_drawn_at_the_top_right_and_is_clickable(self):
        gui = self.gui()
        r = gui.cog_rect
        self.assertGreater(r.right, gui.L.W * 0.9)
        self.assertLess(r.bottom, gui.L.top_h)
        self.assertEqual(gui.hit_at(r.center), ("button", {"name": "settings"}))
        self.assertNotEqual(tuple(gui.screen.get_at(r.center))[:3], tuple(gui.screen.get_at((r.x + 2, r.y + 2)))[:3])

    def test_clicking_it_opens_the_pop_up_with_every_setting(self):
        gui = self.gui()
        open_cog(gui)
        self.assertEqual([n for _r, n in gui.overlay.buttons],
                         ["smaller", "bigger", "bg_prev", "bg_next",                                            # round AD1: Table < Rotate >
                          "full", "motion", "frames",                                                           # round 26
                          "hand_sort",                                                                          # "Sort hand by type"
                          "sound", "hover_tick", "music_on", "ambience_on",        # round 24 (AD1: Sound | Hover tick share a row; AU1: Music | Ambience)
                          "vol_down", "vol_up",
                          "newgame", "concede", "help", "report", "licenses",
                          "data_folder",                                                                        # round 28
                          "tour", "updates"])                                                                   # round UX1, 30

    def test_text_size_buttons_change_the_size_and_keep_the_pop_up_open(self):
        gui = self.gui()
        open_cog(gui)
        before = gui.text_scale
        click(gui, popup_point(gui, "bigger"))
        bigger = gui.text_scale
        self.assertGreater(bigger, before)
        self.assertIsNotNone(gui.overlay)
        click(gui, popup_point(gui, "bigger"))
        self.assertGreater(gui.text_scale, bigger)
        click(gui, popup_point(gui, "smaller"))
        self.assertEqual(gui.text_scale, bigger)
        self.assertIsNotNone(gui.overlay)

    def test_the_switches_flip_and_keep_the_pop_up_open(self):
        gui = self.gui()
        calls = []
        gui.toggle_fullscreen = lambda: calls.append("full")
        open_cog(gui)
        click(gui, popup_point(gui, "full"))
        self.assertEqual(calls, ["full"])
        self.assertIsNotNone(gui.overlay)                      # so you see the switch move
        was = gui.animations
        click(gui, popup_point(gui, "motion"))
        self.assertEqual(gui.animations, not was)
        self.assertIsNotNone(gui.overlay)

    def test_new_game_concede_and_help_do_their_job_and_close_the_pop_up(self):
        gui = self.gui()
        calls = []
        gui.ask_new_game = lambda: calls.append("new")
        gui.ask_concede = lambda: calls.append("concede")
        open_cog(gui)
        click(gui, popup_point(gui, "newgame"))
        self.assertIsNone(gui.overlay)
        open_cog(gui)
        click(gui, popup_point(gui, "concede"))
        self.assertEqual(calls, ["new", "concede"])
        self.assertIsNone(gui.overlay)
        open_cog(gui)
        click(gui, popup_point(gui, "help"))
        self.assertIsNone(gui.overlay)
        self.assertIsInstance(gui.modal, dlg.HelpDialog)

    def test_the_pop_up_has_three_headed_groups_and_the_switches_show_on_or_off(self):
        gui = self.gui()
        open_cog(gui)
        pop = gui.overlay
        rows = dict((n, r) for r, n in pop.buttons)
        self.assertLess(rows["smaller"].y, rows["full"].y)
        self.assertLess(rows["motion"].y, rows["newgame"].y)          # DISPLAY, then GAME ...
        self.assertLess(rows["concede"].y, rows["help"].y)            # ... then HELP
        self.assertGreater(rows["newgame"].y - rows["motion"].bottom, rows["motion"].y - rows["full"].bottom)      # a heading sits between the groups

        def greenness(name):                                          # the most green pixel along the row's right end (the pill)
            r = rows[name]
            return max(px[1] - px[0] for px in (tuple(gui.screen.get_at((x, r.centery)))[:3] for x in range(r.right - 60, r.right - 14)))
        gui.animations = True
        frame(gui, 1)
        on_green = greenness("motion")
        gui.animations = False
        frame(gui, 1)
        off_green = greenness("motion")
        self.assertGreater(on_green, off_green + 30)

    def test_report_a_bug_in_the_pop_up_opens_the_form(self):
        gui = self.gui()
        open_cog(gui)
        click(gui, popup_point(gui, "report"))
        self.assertIsInstance(gui.overlay, fset.ReportDialog)

    def test_clicking_elsewhere_or_pressing_escape_closes_it_without_doing_anything(self):
        gui = self.gui()
        open_cog(gui)
        click(gui, (20, gui.L.H // 2))
        self.assertIsNone(gui.overlay)
        self.assertEqual(gui.session.sent, [])
        open_cog(gui)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.overlay)
        self.assertEqual(gui.session.sent, [])

    def test_a_click_on_the_pop_up_never_reaches_the_game_behind_it(self):
        gui = self.gui()
        open_cog(gui)
        rect = gui.overlay.rect
        click(gui, (rect.x + 4, rect.y + 4))                   # the pop-up's own margin
        self.assertIsNotNone(gui.overlay)
        self.assertEqual(gui.session.sent, [])

    def test_the_keyboard_shortcuts_still_work(self):
        gui = self.gui()
        calls = []
        gui.toggle_fullscreen = lambda: calls.append("full")
        gui.open_menu = lambda: calls.append("menu")
        key(gui, pygame.K_F11)
        key(gui, pygame.K_n, mod=pygame.KMOD_CTRL)
        self.assertEqual(calls, ["full", "menu"])
        before = gui.text_scale
        key(gui, pygame.K_PLUS)
        self.assertGreater(gui.text_scale, before)
        key(gui, pygame.K_MINUS)
        self.assertEqual(gui.text_scale, before)
        key(gui, pygame.K_h)
        self.assertIsInstance(gui.modal, dlg.HelpDialog)

    def test_the_pop_up_and_its_buttons_fit_the_window_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.gui(size=size, scale=scale)
                open_cog(gui)
                frame(gui)
                window = gui.screen.get_rect()
                self.assertTrue(window.contains(gui.overlay.rect), gui.overlay.rect)
                for rect, name in gui.overlay.buttons:
                    self.assertTrue(gui.overlay.rect.contains(rect), (name, rect))

    def test_the_tooltip_says_what_the_cog_is_and_stays_quiet_while_it_is_open(self):
        gui = self.gui()
        move(gui, gui.cog_rect.center)
        self.assertIn("Settings", gui.tooltip_text())
        open_cog(gui)
        self.assertEqual(gui.tooltip_text(), "")

    def test_the_help_window_lists_the_cog_and_f8(self):
        keys = [k for k, _t in ft.HELP_LINES]
        self.assertTrue(any("Cog" in k for k in keys))
        self.assertTrue(any("F8" in k for k in keys))


class ReportFormTests(Base):
    def open(self, **kw):
        gui = self.gui(**kw)
        key(gui, pygame.K_F8)
        self.assertIsInstance(gui.overlay, fset.ReportDialog)
        return gui

    def fill(self, gui, name="Friend A", happened="The mirror copied itself"):
        form = gui.overlay
        form.fields[0].text, form.fields[1].text = name, happened
        frame(gui)

    def test_f8_opens_the_form_and_escape_closes_it(self):
        gui = self.open()
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.overlay)

    def test_typing_goes_into_the_focused_box_and_never_into_the_game(self):
        gui = self.open()
        scale = gui.text_scale
        key(gui, pygame.K_TAB)                                      # name -> what happened
        gui.overlay.focus = 1
        type_text(gui, "a e u + - h  ok")
        self.assertEqual(gui.overlay.fields[1].text, "a e u + - h  ok")
        self.assertEqual(gui.session.sent, [])
        self.assertEqual(gui.text_scale, scale)
        self.assertIsNone(gui.modal)
        key(gui, pygame.K_BACKSPACE)
        self.assertEqual(gui.overlay.fields[1].text, "a e u + - h  o")

    def test_tab_and_shift_tab_move_between_the_boxes_and_clicking_picks_one(self):
        gui = self.open()
        gui.overlay.focus = 0
        key(gui, pygame.K_TAB)
        self.assertEqual(gui.overlay.focus, 1)
        key(gui, pygame.K_TAB)
        key(gui, pygame.K_TAB)
        self.assertEqual(gui.overlay.focus, 0)
        key(gui, pygame.K_TAB, mod=pygame.KMOD_SHIFT)
        self.assertEqual(gui.overlay.focus, 2)
        click(gui, gui.overlay.fields[1].rect.center)
        self.assertEqual(gui.overlay.focus, 1)

    def test_enter_adds_a_line_in_the_big_boxes_and_moves_on_from_the_name(self):
        gui = self.open()
        gui.overlay.focus = 0
        type_text(gui, "Sam")
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.overlay.focus, 1)
        self.assertEqual(gui.overlay.fields[0].text, "Sam")
        type_text(gui, "one\ntwo")
        self.assertEqual(gui.overlay.fields[1].text, "one\ntwo")

    def test_paste_uses_the_clipboard_and_cleans_it(self):
        gui = self.open()
        gui.read_clipboard = lambda: "pasted\r\nline\x00 two"
        gui.overlay.focus = 1
        key(gui, pygame.K_v, mod=pygame.KMOD_CTRL)
        self.assertEqual(gui.overlay.fields[1].text, "pasted\nline two")

    def test_a_long_word_and_a_long_text_do_not_break_the_form(self):
        gui = self.open()
        gui.overlay.fields[1].text = "x" * 1400 + "\n" + "word " * 200
        frame(gui, 2)
        gui.overlay.fields[1].add("y" * 5000)
        self.assertEqual(len(gui.overlay.fields[1].text), reporting.FIELD_LIMIT)

    def test_saving_is_only_possible_once_something_is_written(self):
        gui = self.open()
        frame(gui)
        self.assertNotIn("save", [n for _r, n in gui.overlay.buttons])
        key(gui, pygame.K_RETURN, mod=pygame.KMOD_CTRL)
        self.assertEqual(gui.overlay.phase, "form")
        self.fill(gui, happened="bug")
        self.assertNotIn("save", [n for _r, n in gui.overlay.buttons])
        self.fill(gui, happened="the button did nothing")
        self.assertIn("save", [n for _r, n in gui.overlay.buttons])

    def test_saving_without_discord_writes_the_zip_and_says_where_it_is(self):
        gui = self.open()
        self.fill(gui)
        self.assertEqual([n for _r, n in gui.overlay.buttons if n in ("send", "save")], ["save"])
        click(gui, popup_point(gui, "save"))
        form = gui.overlay
        self.assertEqual(form.phase, "saved")
        self.assertTrue(os.path.isfile(form.path))
        self.assertTrue(form.path.startswith(os.path.join(self.folder, "bug_reports")))
        opened = []
        real = reporting.open_folder
        reporting.open_folder = lambda p: opened.append(p) or True
        self.addCleanup(lambda: setattr(reporting, "open_folder", real))
        click(gui, popup_point(gui, "folder"))
        self.assertEqual(opened, [os.path.dirname(form.path)])
        click(gui, popup_point(gui, "close"))
        self.assertIsNone(gui.overlay)

    def test_the_zip_holds_a_real_screenshot_the_board_and_the_clicks(self):
        import zipfile
        gui = self.open()
        gui.session.sent_log.append((time.time(), {"c": "card", "id": 7}))
        gui.overlay.context = gui.report_context()
        self.fill(gui)
        click(gui, popup_point(gui, "save"))
        with zipfile.ZipFile(gui.overlay.path) as z:
            names = set(z.namelist())
            self.assertTrue({"report.txt", "state.json", "game_log.txt", "commands.json"} <= names, names)
            shot = [n for n in names if n.startswith("screenshot.")]
            self.assertEqual(len(shot), 1)
            data = z.read(shot[0])
            self.assertTrue(data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG"), data[:8])
            self.assertGreater(len(data), 2000)
            self.assertEqual(json.loads(z.read("commands.json"))["commands"][0]["id"], 7)
            self.assertIn("The mirror copied itself", z.read("report.txt").decode())

    def test_the_screenshot_is_the_table_not_the_pop_up_that_asked_for_it(self):
        gui = self.gui()
        baseline = gui.capture_frame()
        open_cog(gui)
        p = (gui.overlay.rect.centerx, gui.overlay.rect.centery)
        shot = gui.capture_frame()
        self.assertEqual(tuple(shot.get_at(p)), tuple(baseline.get_at(p)))
        self.assertIsInstance(gui.overlay, fset.SettingsPopup)                  # and the pop-up is still there

    def test_the_form_opens_on_top_of_a_question_from_forge_and_leaves_it_alone(self):
        gui = self.gui()
        SearchDialogTests("library_request").library_request(gui)
        modal = gui.modal
        self.assertIsNotNone(modal)
        key(gui, pygame.K_F8)
        self.assertIsInstance(gui.overlay, fset.ReportDialog)
        self.assertIs(gui.modal, modal)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.overlay)
        self.assertIs(gui.modal, modal)
        self.assertEqual(gui.session.commands("answer"), [])

    def test_the_form_can_open_before_any_game_started(self):
        gui = self.gui(state=None)
        key(gui, pygame.K_F8)
        self.fill(gui)
        click(gui, popup_point(gui, "save"))
        self.assertEqual(gui.overlay.phase, "saved")

    def test_the_players_name_is_remembered_for_next_time(self):
        path = os.path.join(self.folder, "settings.json")
        gui = self.open(settings=path)
        self.fill(gui, name="Friend A")
        click(gui, popup_point(gui, "save"))
        with open(path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["reporter_name"], "Friend A")
        again = self.gui(settings=path)
        key(again, pygame.K_F8)
        self.assertEqual(again.overlay.fields[0].text, "Friend A")
        self.assertEqual(again.overlay.focus, 1)

    def test_form_and_result_fit_the_window_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.gui(size=size, scale=scale)
                key(gui, pygame.K_F8)
                self.fill(gui, happened="text " * 80)
                window = gui.screen.get_rect()
                self.assertTrue(window.contains(gui.overlay.rect), gui.overlay.rect)
                for rect, name in gui.overlay.buttons:
                    self.assertTrue(gui.overlay.rect.contains(rect), (name, rect))
                for f in gui.overlay.fields:
                    self.assertTrue(gui.overlay.rect.contains(f.rect), f.rect)
                click(gui, popup_point(gui, "save"))
                self.assertTrue(window.contains(gui.overlay.rect))
                for rect, name in gui.overlay.buttons:
                    self.assertTrue(gui.overlay.rect.contains(rect), (name, rect))

    def test_a_crash_while_writing_becomes_a_message_not_a_crash(self):
        gui = self.open()
        gui.overlay.folder = os.path.join(self.folder, "blocker", "x")
        with open(os.path.join(self.folder, "blocker"), "w") as f:
            f.write("a file where a folder should be")
        self.fill(gui)
        click(gui, popup_point(gui, "save"))
        self.assertEqual(gui.overlay.phase, "failed")
        self.assertIn("could not be written", gui.overlay.message)
        frame(gui, 2)


class SendingTests(Base):
    def setUp(self):
        super().setUp()
        self.set_webhook()
        self.posts = []
        self.reply = (True, "sent", "Sent! Your bug report has been submitted.")
        real = reporting.post_to_discord

        def fake(url, text, zip_path=None, **kw):
            self.posts.append((url, text, zip_path))
            return self.reply
        reporting.post_to_discord = fake
        self.addCleanup(lambda: setattr(reporting, "post_to_discord", real))

    def open(self):
        gui = self.gui()
        key(gui, pygame.K_F8)
        gui.overlay.fields[0].text, gui.overlay.fields[1].text = "Friend A", "the mirror copied itself"
        frame(gui)
        return gui

    def wait(self, gui, phase):
        end = time.time() + 5
        while gui.overlay.phase != phase and time.time() < end:
            frame(gui)
            time.sleep(0.01)
        self.assertEqual(gui.overlay.phase, phase, gui.overlay.message)

    def labels(self, gui):
        """The words on every button drawn during one frame."""
        seen, real = [], gui.draw_button

        def spy(rect, label, *a, **kw):
            seen.append(label)
            return real(rect, label, *a, **kw)
        gui.draw_button = spy
        try:
            frame(gui)
        finally:
            del gui.draw_button
        return seen

    def test_with_a_webhook_the_form_has_one_submit_button_and_no_save_only(self):
        gui = self.open()
        self.assertEqual([n for _r, n in gui.overlay.buttons if n in ("send", "save")], ["send"])
        labels = self.labels(gui)
        self.assertIn("Submit", labels)
        self.assertIn("Cancel", labels)
        self.assertFalse([l for l in labels if l.startswith(("Save", "Send"))], labels)      # no "Save file only", no "Send to Karl"

    def test_send_posts_the_zip_and_a_summary_then_says_thanks(self):
        gui = self.open()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        url, text, path = self.posts[0]
        self.assertEqual(url, WEBHOOK)
        self.assertIn("Friend A", text)
        self.assertIn("the mirror copied itself", text)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(reporting.seconds_until_send_allowed(), 0)

    def test_a_second_send_has_to_wait(self):
        gui = self.open()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "sent")
        click(gui, popup_point(gui, "close"))
        key(gui, pygame.K_F8)
        gui.overlay.fields[1].text = "another problem"
        frame(gui)
        self.assertNotIn("send", [n for _r, n in gui.overlay.buttons])
        self.assertNotIn("save", [n for _r, n in gui.overlay.buttons])
        self.assertTrue(any(l.startswith("Submit (wait") for l in self.labels(gui)))
        key(gui, pygame.K_RETURN, mod=pygame.KMOD_CTRL)                        # the shortcut respects the wait too
        self.assertEqual(len(self.posts), 1)

    def test_when_sending_fails_the_zip_is_kept_and_the_player_told_what_to_do(self):
        self.reply = (False, "offline", "Could not reach Discord (no internet connection?).")
        gui = self.open()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "failed")
        self.assertTrue(os.path.isfile(gui.overlay.path))
        self.assertIn("internet", gui.overlay.message)
        names = [n for _r, n in gui.overlay.buttons]
        self.assertIn("folder", names)
        self.assertIn("retry", names)
        self.assertEqual(reporting.seconds_until_send_allowed(), 0)

    def test_try_again_re_sends_the_same_zip(self):
        self.reply = (False, "offline", "Could not reach Discord (no internet connection?).")
        gui = self.open()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "failed")
        first = gui.overlay.path
        self.reply = (True, "sent", "Sent!")
        click(gui, popup_point(gui, "retry"))
        self.wait(gui, "sent")
        self.assertEqual([p[2] for p in self.posts], [first, first])
        self.assertEqual(len(os.listdir(os.path.join(self.folder, "bug_reports"))), 1)

    def test_a_deleted_webhook_offers_no_retry(self):
        self.reply = (False, "bad_webhook", "Discord no longer accepts this webhook address (it may have been deleted).")
        gui = self.open()
        click(gui, popup_point(gui, "send"))
        self.wait(gui, "failed")
        self.assertNotIn("retry", [n for _r, n in gui.overlay.buttons])

    def test_the_only_way_to_finish_the_form_with_a_webhook_is_to_submit(self):
        gui = self.open()
        self.assertNotIn("save", [n for _r, n in gui.overlay.buttons])
        key(gui, pygame.K_RETURN, mod=pygame.KMOD_CTRL)                        # the keyboard shortcut submits too
        self.wait(gui, "sent")
        self.assertEqual(len(self.posts), 1)

    def test_the_form_cannot_be_closed_by_enter_while_it_is_sending(self):
        gui = self.open()
        gui.overlay.phase = "sending"
        gui.overlay.job = SimpleNamespace(finished=False)
        key(gui, pygame.K_RETURN)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNotNone(gui.overlay)
        frame(gui, 2)

    def test_a_bad_address_in_the_config_falls_back_to_saving_a_file(self):
        self.set_webhook("https://example.com/not-discord")
        gui = self.open()
        self.assertEqual([n for _r, n in gui.overlay.buttons if n in ("send", "save")], ["save"])


class PlumbingTests(unittest.TestCase):
    def test_the_session_remembers_the_newest_commands_it_sent(self):
        s = fc.ForgeSession("d.dck", [])
        s.proc = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
        s.click_card(5)
        self.assertEqual(s.sent_log[-1][1], {"c": "card", "id": 5})
        for i in range(500):
            s.click_card(i)
        self.assertEqual(len(s.sent_log), 400)
        self.assertEqual(s.sent_log[-1][1]["id"], 499)

    def test_report_test_prints_and_exits_with_the_right_code(self):
        real = reporting.send_test
        self.addCleanup(lambda: setattr(reporting, "send_test", real))
        for ok, code in ((True, 0), (False, 1)):
            reporting.send_test = lambda folder=None, name=None, ok=ok: (ok, "message")
            with self.assertRaises(SystemExit) as cm:
                ft.main(["forge_table.py", "--report-test"])
            self.assertEqual(cm.exception.code, code)

    def test_report_context_turns_the_session_into_plain_facts(self):
        gui = make_gui()
        gui.session.sent_log.extend([(100.0, {"c": "ok"}), (102.5, {"c": "card", "id": 3})])
        ctx = gui.report_context()
        self.assertEqual(ctx["commands"], [(0.0, {"c": "ok"}), (2.5, {"c": "card", "id": 3})])
        self.assertTrue(all(isinstance(x, str) for x in ctx["log_lines"]))
        self.assertIs(ctx["state"], gui.state)
        data, ext = ctx["screenshot"](800)
        self.assertIn(ext, ("jpg", "png"))
        self.assertGreater(len(data), 1000)


if __name__ == "__main__":
    unittest.main()
