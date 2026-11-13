# SPDX-License-Identifier: GPL-3.0-or-later
"""Release candidate 0.28.53.17 (task rc17, 8 Nov 2026; Karl, 8 Nov: "Queue it now"): the places the merged branches met, one
check per merged change, and the version.

  1  MergeTests    The two places merged by hand: rpg_screens.py's town stage (claude/round-shopmsg1 forks from 0.28.53.15, before
                   PROG4's Gear tab: the full-width tabs and the tabs without the "point at a card" hint are packs, gear AND
                   singles, and the message strip is SHOPMSG2's growing one, drawn last) and forge_table.prompt_view (rc16b's
                   free_first beside bug-1568353208475066370's about=; the action bar passes both). And the bridge jar, rebuilt
                   once from every merged source.
  2  ChangeTests   One check per merged change: the Singles gallery's whole-rows rule, the right panels' saved layout, the
                   multiplayer stop rule (an opponent's spell asks hasAnswer like a trigger), the optional-cost question's
                   words, RESUME2's out guests and game over resent on rejoin, RELAYPW1's password from secrets.
  3  ArchidektTests  ARCH1, added 12 Nov 2026 (Karl: "goes into 0.28.53.17 before I release it"): an Archidekt line with a label
                   after its category tag - the commander's tag is found, the label leaves the name.
  4  VersionTests  0.28.53.17 with its history line, ARCH1 named in it.
"""
import ast
import os
import re
import sys
import unittest
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def read(*parts):
    with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
        return f.read()


def method_source(path, cls, name):
    """The source of one method (or a module function when cls is None)."""
    src = read(path)
    tree = ast.parse(src)
    body = tree.body
    if cls:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, node)


class MergeTests(unittest.TestCase):
    def town(self):
        return method_source("rpg_screens.py", "AdventureFlow", "draw_town")

    def test_full_width_tabs_are_packs_gear_and_singles(self):
        src = self.town()
        m = re.search(r"share = 1\.0 if self\.tab in \(([^)]*)\) else", src)
        self.assertIsNotNone(m, "the town stage's share line")
        self.assertEqual(set(re.findall(r'"(\w+)"', m.group(1))), {"packs", "gear", "singles"})

    def test_no_hover_hint_on_packs_gear_and_singles(self):
        src = self.town()
        m = re.search(r"if self\.hover is None and self\.tab not in \(([^)]*)\):", src)
        self.assertIsNotNone(m, "the town stage's hint line")
        self.assertEqual(set(re.findall(r'"(\w+)"', m.group(1))), {"packs", "gear", "singles"})

    def test_the_message_strip_is_shopmsg2s_and_drawn_last(self):
        src = self.town()
        self.assertIn("self.message_lines(gui, msg_w, self.MESSAGE_MAX_LINES)", src)
        self.assertNotIn("msg_h = 2 * small.get_height() + self.gap", src)       # PROG4's fixed two lines
        strip = src.index("self.draw_message(gui, self.msg_strip.x")
        self.assertGreater(strip, src.index('getattr(self, "draw_tab_" + self.tab)(gui, area)'))

    def test_prompt_view_takes_free_first_and_about(self):
        import forge_table as ft
        keep = "Do you want to keep your hand?"
        _h, hint, _ok = ft.prompt_view(keep, "Me", 0, free_first=False, about="Ignored here")
        self.assertIn("puts 1 on the bottom", hint)
        _h, hint, _ok = ft.prompt_view(keep, "Me", 0, free_first=True)
        self.assertIn("the first is free", hint)
        head, hint, ok = ft.prompt_view("Do you want to pay 2 life?", "Me", 0, kind="confirm", free_first=False,
                                        about="The Call of the Ring")
        self.assertEqual(head, "Pay 2 life?  -  The Call of the Ring")
        self.assertEqual(hint, ft.COST_HINT)

    def test_the_action_bar_passes_both(self):
        src = method_source("forge_table.py", "ForgeTable", "draw_action_bar")
        call = src[src.index("prompt_view("):src.index('if s.get("prompt")')]
        self.assertIn("free_first=self.first_mulligan_free()", call)
        self.assertIn("about=self.cost_card_name()", call)

    def test_the_bridge_jar_is_built_from_these_sources(self):
        import setup_forge
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            stamp = z.read("bridge_source.sha256").decode().strip()
        self.assertEqual(stamp, setup_forge.bridge_source_hash())


class ChangeTests(unittest.TestCase):
    def test_singles_gallery_counts_only_whole_rows(self):
        import rpg_screens as rs
        *_rest, rows, fit = rs.gallery_shape(30, 800, 40, 60, 8, 1.0, 0.716, 300, 12)
        self.assertEqual(fit, 0)                     # not even one row fits: none is drawn (SHOPMSG2)
        cw, ch, _p, _c, row_h, rows, fit = rs.gallery_shape(600, 800, 40, 60, 8, 1.0, 0.716, 300, 12)
        self.assertEqual(fit, 600 // row_h)
        self.assertGreaterEqual(fit, 2)

    def test_singles_tab_is_a_gallery(self):
        src = method_source("rpg_screens.py", "AdventureFlow", "draw_tab_singles")
        self.assertIn("gallery_shape(", src)
        self.assertIn("draw_single_details", src)

    def test_right_panels_layout_is_remembered(self):
        import forge_table as ft
        fold, size = ft.right_panels_from(None)
        self.assertEqual(fold, {k: False for k in ft.PANEL_NAMES})
        self.assertEqual(set(ft.PANEL_NAMES), {"focus", "stack", "log"})
        self.assertIn('"right_panels"', read("forge_table.py"))

    def test_an_opponents_spell_asks_has_answer(self):
        src = read("java_bridge", "src", "forge", "bridge", "Passing.java")
        decide = src[src.index("Decision decide("):src.index("long ta = Timing.start();")]
        self.assertNotRegex(decide, r"if \(top\.isSpell\(\)\)\s*\{\s*return Decision\.DEFER;",
                            "an opponent's spell must not stop you whenever you could do anything")
        self.assertIn('return top.isSpell() ? "Spell" : top.isTrigger() ? "Triggered" : "Activated";', src)

    def test_optional_cost_words(self):
        import forge_table as ft
        self.assertEqual(ft.optional_cost_text("Do you want to pay 2 life?", "confirm"), "Pay 2 life")
        self.assertIsNone(ft.optional_cost_text("Do you want to pay 2 life?", "priority"))
        self.assertIn("Space and Enter don't answer it", ft.COST_HINT)

    def test_resume2_out_guests_and_game_over_on_rejoin(self):
        src = read("java_bridge", "src", "forge", "bridge", "NetHost.java")
        self.assertIn("markOutSeats(", src)
        self.assertIn('m.add("out", markOutSeats(', src)

    def test_relay_password_from_secrets(self):
        src = read("tools", "online_soak.py")
        self.assertIn("fn.make_password(secrets.SystemRandom()) if relay else PASSWORD", src)


class ArchidektTests(unittest.TestCase):
    def test_a_commander_tag_before_a_label_is_found(self):
        import deck_importer as di
        lines = ["1x Accursed Marauder (mh3) 405 [Removal]"] + [f"1x Test Card {i:02d} (tst) {i} [Removal]" for i in range(74)]
        lines += ["1x Tergrid, God of Fright // Tergrid's Lantern (khm) 112 [Commander{top}] ^Getting,#2ccce4^",
                  "24x Swamp (trk) 321 [Land]"]
        cmd, deck = di.import_from_text("\n".join(lines) + "\n")
        self.assertEqual(cmd, ["Tergrid, God of Fright // Tergrid's Lantern"])
        self.assertEqual(len(deck), 99)

    def test_a_label_leaves_the_name(self):
        import deck_importer as di
        _, deck = di.import_from_text("1x Crypt Ghast (rvr) 322 [Ramp] ^Don't Have,#f47373^\n1x Sol Ring\n")
        self.assertEqual(sorted(deck), ["Crypt Ghast", "Sol Ring"])


class VersionTests(unittest.TestCase):
    def test_version(self):
        import version
        self.assertEqual(version.VERSION, "0.28.53.17")
        src = read("version.py")
        self.assertIn('0.28.53.17 Release candidate (Karl, 8 Nov 2026: "Queue it now")', src)
        self.assertIn("ARCH1 (an Archidekt text export", src)


if __name__ == "__main__":
    unittest.main()
