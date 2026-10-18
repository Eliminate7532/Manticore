# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CTR1 (16 Oct 2026): counter names as a person reads them (ROADMAP: "Counter names other than +1/+1 and -1/-1 are shown as
Forge names them (mapping is assumed, unchecked)").

The bridge sends CounterType.toString() - for Forge's CounterEnumType that is the Java constant ("M0M1", "ACQUIREDTASTE"). The card's
badge showed str.title() of it ("M0M1", "Acquiredtaste") and the floating "+2" showed no word at all for most counters. Now both use
counter_names, which gives Forge's own getName() (tests/fixtures/ctr1/counter_enum_fb4d809.json, read from Forge's source at fb4d809).

  NameTests    every one of Forge's 237 constants, keyword counters, a script's own counter, nothing
  JarTests     the fixture lists exactly the constants in forge_runtime/forge.jar (needs javap and the runtime)
  TableTests   the badge on a card and the floating number say the words
  LiveTests    what the bridge really sends for an M0M1, an ACQUIREDTASTE and a keyword counter put on a card (real Forge)
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import counter_names as cn
import events
import forge_client as fc
import forge_table as ft
from tests import live
from tests.test_forge_table import make_gui

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "ctr1", "counter_enum_fb4d809.json")


def forge_counters():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)["counters"]


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), time.monotonic())


class NameTests(unittest.TestCase):
    def test_every_forge_counter_reads_as_forge_names_it(self):
        counters = forge_counters()
        self.assertEqual(len(counters), 237)
        for e in counters:
            with self.subTest(counter=e["enum"]):
                want = e["name"][0].upper() + e["name"][1:]          # Forge's getName(), its first letter capital
                self.assertEqual(cn.counter_name(e["enum"]), want)

    def test_the_ones_the_table_used_to_get_wrong(self):
        for raw, want in (("M0M1", "-0/-1"), ("P2P2", "+2/+2"), ("P1P0", "+1/+0"), ("ACQUIREDTASTE", "Acquired taste"),
                          ("P1P1", "+1/+1"), ("M1M1", "-1/-1"), ("LOYALTY", "Loyalty"), ("CELL", "Cell")):
            with self.subTest(raw=raw):
                self.assertEqual(cn.counter_name(raw), want)

    def test_keyword_counters_and_a_script_s_own_name(self):
        """A keyword counter's toString() is the keyword as the script writes it (CounterKeywordType: keyword.original()). A
        ":" parameter would show the keyword only (a guard: no script at fb4d809 writes one). A counter a script names itself
        (CounterCustomType) keeps its spelling, capitalised like Forge's own when it is all capitals."""
        for raw, want in (("Flying", "Flying"), ("First Strike", "First Strike"), ("Hexproof:Card.Black:black", "Hexproof"),
                          ("Trample:Planeswalker", "Trample"), ("RIBBON", "Ribbon"), ("Doom", "Doom"), ("rope", "Rope")):
            with self.subTest(raw=raw):
                self.assertEqual(cn.counter_name(raw), want)

    def test_nothing_is_nothing(self):
        for raw in (None, "", "  "):
            self.assertEqual(cn.counter_name(raw), "")
            self.assertEqual(cn.float_words(raw), "")

    def test_float_words_lower_the_words_but_not_the_numbers(self):
        self.assertEqual(cn.float_words("ACQUIREDTASTE"), "acquired taste")
        self.assertEqual(cn.float_words("M0M1"), "-0/-1")
        self.assertEqual(cn.float_words("Flying"), "flying")


class JarTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("javap"), "needs a JDK (javap)")
    def test_the_fixture_lists_exactly_forge_s_constants(self):
        """A Forge update that adds a counter with a name of its own shows up here (then regenerate the fixture from Forge's
        CounterEnumType.java and add any new name to counter_names.NAMED if the rule doesn't give it)."""
        jar = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not os.path.isfile(jar):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        r = subprocess.run([shutil.which("javap"), "-cp", jar, "forge.game.card.CounterEnumType"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        prefix = "public static final forge.game.card.CounterEnumType "
        in_jar = {ln.strip()[len(prefix):].rstrip(";") for ln in r.stdout.splitlines() if ln.strip().startswith(prefix)}
        self.assertEqual(in_jar, {e["enum"] for e in forge_counters()})


class TableTests(unittest.TestCase):
    def test_the_badge_on_a_card_says_the_words(self):
        """What a badge is asked to say (clip_text's input) and what it draws. A long name is shortened to fit 55% of the card
        ("Acquire..." - as "Acquiredtaste" was before); the words before shortening are Forge's."""
        gui = make_gui()
        asked, drawn = [], []
        real_clip, real_draw = ft.clip_text, ft.draw_text

        def spy_clip(text, *a, **k):
            asked.append(text)
            return real_clip(text, *a, **k)

        def spy_draw(surf, text, *a, **k):
            drawn.append(text)
            return real_draw(surf, text, *a, **k)
        card = {"id": 1, "name": "Grizzly Bears", "isCreature": True, "power": 2, "toughness": 2,
                "counters": {"M0M1": 2, "ACQUIREDTASTE": 1, "P1P1": 3}}
        surf = pygame.Surface((300, 420))
        with mock.patch.object(ft, "clip_text", spy_clip), mock.patch.object(ft, "draw_text", spy_draw):
            gui._badges(surf, card, 300, 420)
        for want in ("2 x -0/-1", "Acquired taste", "3 x +1/+1"):
            self.assertIn(want, asked)
        for want in ("2 x -0/-1", "3 x +1/+1"):
            self.assertIn(want, drawn)
        for wrong in ("M0M1", "2 x M0M1", "Acquiredtaste", "P1P1"):
            self.assertNotIn(wrong, asked)
            self.assertNotIn(wrong, drawn)

    def test_the_floating_number_says_the_words(self):
        gui = make_gui("main1_lands")
        gui.animations = True
        cid = gui.session.opponents()[0]["zones"]["battlefield"][0]["id"]
        now = time.monotonic()
        cases = (("M0M1", 0, 2, "+2 -0/-1"), ("ACQUIREDTASTE", 0, 1, "+1 acquired taste"), ("ACORN", 1, 0, "-1 acorn"),
                 ("LOYALTY", 3, 5, "+2 loyalty"), ("P1P1", 0, 2, "+2 +1/+1"), (None, 0, 1, "+1"))
        for raw, old, new, want in cases:
            gui.anim.floats.clear()
            gui.anim.feed(gui, [beat("counters", card=cid, counter=raw, old=old, new=new)], now)
            with self.subTest(counter=raw):
                self.assertEqual([f[0] for f in gui.anim.floats], [want])


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    """The bridge's snapshot, not a guess: two Grizzly Bears put on my battlefield with Forge's own Setup Game State lines (a dev
    set-up, as card_check does it) - one with "Counters:M0M1=2,ACQUIREDTASTE=1,P1P1=1", one with a Flying keyword counter written as
    card scripts write it ("CounterType$ Flying", 21 scripts at fb4d809) - then the "counters" the bridge sends for them."""

    def test_the_bridge_sends_forge_s_own_counter_names(self):
        fc.sync_bridge()
        tmp = tempfile.mkdtemp(prefix="ctr1_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        deck = ["Forest"] * 17 + ["Grizzly Bears"] * 23
        p0 = fc.write_deck_file(os.path.join(tmp, "p.dck"), [], deck, "P", fmt="standard")
        p1 = fc.write_deck_file(os.path.join(tmp, "o.dck"), [], deck, "O", fmt="standard")
        s = fc.ForgeSession(p0, [p1], name="Tester", seed=3, dev=True)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 150
        while time.time() < end:                              # to my first priority question, keeping the opening hand
            s.poll()
            self.assertFalse(s.fatal or s.exited, s.fatal or "Forge stopped while starting")
            st = s.state
            msg = (st.get("prompt") or {}).get("message", "") if st else ""
            if st and s.is_mulligan_prompt():
                s.ok()
                time.sleep(0.5)
            elif st and msg.startswith("Priority") and st.get("asking", True):
                break
            time.sleep(0.1)
        else:
            self.fail("never reached my first priority question")
        before = s.setups_done
        s.setup(["humanlife=20", "ailife=20", "activeplayer=human", "activephase=MAIN1", "turn=3",
                 "humanbattlefield=Grizzly Bears|Counters:M0M1=2,ACQUIREDTASTE=1,P1P1=1;Grizzly Bears|Counters:Flying=1",
                 "aibattlefield=Forest"])
        seen = []
        end = time.time() + 60
        while time.time() < end and len(seen) < 2:
            s.poll()
            if s.setups_done > before:
                seen = [c for c in (s.me() or {}).get("zones", {}).get("battlefield", [])
                        if c.get("name") == "Grizzly Bears" and c.get("counters")]
            time.sleep(0.1)
        self.assertEqual(getattr(s, "setup_failures", []), [])
        self.assertEqual(len(seen), 2, "the set-up never showed both Bears with counters")
        counters = {}
        for c in seen:
            counters.update(c["counters"])
        print("\n  live counters from the bridge:", counters, "->", {k: cn.counter_name(k) for k in counters})
        self.assertEqual(counters, {"M0M1": 2, "ACQUIREDTASTE": 1, "P1P1": 1, "Flying": 1})
        self.assertEqual({k: cn.counter_name(k) for k in counters},
                         {"M0M1": "-0/-1", "ACQUIREDTASTE": "Acquired taste", "P1P1": "+1/+1", "Flying": "Flying"})
        pt = sorted((c.get("power"), c.get("toughness")) for c in seen)
        self.assertEqual(pt, [(2, 2), (3, 1)])                    # 2/2 with -0/-2 and +1/+1 is 3/1: Forge read M0M1 as -0/-1


if __name__ == "__main__":
    unittest.main()
