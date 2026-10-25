# SPDX-License-Identifier: GPL-3.0-or-later
"""Round BLK1: the table says WHICH attacker a creature that must block can block (the soak of 19-20 Oct, game 58).

Forge's refusal names the blocker only: "Spirit Token (319) must block an attacker, but has not been assigned to block any."
With The Masamune on Priest of the Crossing ("must be blocked if able"), the token can block nothing but Priest, and clicking
it while another attacker is the one being blocked does nothing (CombatUtil.canBlock). A person had no way to tell which.

  SourceTests   the bridge works the list out with Forge's own canBlock on Swing's thread and sends it as "canBlock"
  WhyTests      forge_why names the attacker(s) to click, and keeps its old words when the snapshot doesn't say
  TableTests    the table's explanation dialog shows it, from game 58's real state
  LiveTests     real Forge: the snapshot's canBlock during a Masamune block, the explanation, and blocking that way works
"""
import copy
import json
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_why as why
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
FIX = os.path.join(BASE, "tests", "fixtures", "soak", "blk1_masamune_block.json")
TOKEN, PRIEST, WOE = 319, 138, 133
REFUSAL = "Spirit Token (319) must block an attacker, but has not been assigned to block any."


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def game58(can_block=None):
    """Game 58's block state (the same trimmed state as Round SB3's fixture), with the token's canBlock set when given."""
    with open(FIX, encoding="utf-8") as f:
        st = json.load(f)["state"]
    if can_block is not None:
        for c in st["players"][0]["zones"]["battlefield"]:
            if c["id"] == TOKEN:
                c["canBlock"] = list(can_block)
    return st


def finder(state):
    cards = {c["id"]: c for p in state["players"] for c in p["zones"]["battlefield"]}
    return cards.get


class SourceTests(unittest.TestCase):
    def test_the_bridge_asks_forges_own_canblock(self):
        gui = read("BridgeGui.java")
        body = gui[gui.index("void refreshBlockOptions()"):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("forge.game.combat.CombatUtil.canBlock(attacker, blocker, combat)", body)
        self.assertIn("instanceof forge.gamemodes.match.input.InputBlock", body)
        self.assertIn("blocker.isTapped()", body)
        self.assertIn("catch (RuntimeException e)", body)          # never an error, never a lost snapshot

    def test_it_runs_on_swings_thread_after_every_click_and_question(self):
        main = read("Main.java")
        self.assertRegex(main, r"if \(STALE_CHECKED\.contains\(c\)\) \{\s*gui\.refreshBlockOptions\(\);")
        self.assertIn("javax.swing.SwingUtilities.invokeLater(this::refreshBlockOptions)", read("BridgeGui.java"))

    def test_the_snapshot_reads_the_finished_map_only(self):
        snap = read("Snapshot.java")
        self.assertIn('o.add("canBlock", ca)', snap)
        self.assertNotIn("CombatUtil", snap)                       # Forge's rules are never run on the snapshot thread

    def test_the_jar_has_it(self):
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            data = z.read("forge/bridge/BridgeGui.class")
        self.assertIn(b"refreshBlockOptions", data)
        self.assertIn(b"blockOptions", data)


class WhyTests(unittest.TestCase):
    def test_game_58_names_priest_of_the_crossing(self):
        st = game58([PRIEST])
        e = why.explain(REFUSAL, finder(st))
        self.assertEqual(e.card_id, TOKEN)
        self.assertIn("Right now Spirit Token can block only Priest of the Crossing: click Priest of the Crossing, then "
                      "Spirit Token, then OK.", e.body)
        self.assertNotRegex(e.body, r"\(\d+\)")

    def test_several_attackers_and_two_of_one_name(self):
        st = game58([332, 334, 111])           # two Zombie Tokens and Falkenrath Noble
        e = why.explain(REFUSAL, finder(st))
        self.assertIn("Right now Spirit Token can block a Zombie Token or Falkenrath Noble. Click one of them, then Spirit "
                      "Token, then OK.", e.body)

    def test_without_the_bridges_list_the_old_words_stay(self):
        for can in (None, []):
            e = why.explain(REFUSAL, finder(game58(can)))
            self.assertIn("Assign Spirit Token to block an attacker, then press OK.", e.body)
            self.assertNotIn("Right now", e.body)

    def test_blocks_each_combat_names_them_too(self):
        st = game58([PRIEST])
        e = why.explain("Spirit Token (319) must block each combat but was not assigned to block any attacker now.", finder(st))
        self.assertIn("blocks each combat if able", e.body)
        self.assertIn("can block only Priest of the Crossing", e.body)


class TableTests(unittest.TestCase):
    def test_the_dialog_says_which_attacker(self):
        import forge_dialogs as dlg
        from tests.test_forge_table import frame, load_state, make_gui
        st = copy.deepcopy(load_state("main1_start"))          # a whole snapshot the table can draw, with game 58's two cards
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        g = {c["id"]: c for p in game58([PRIEST])["players"] for c in p["zones"]["battlefield"]}
        token, priest = copy.deepcopy(g[TOKEN]), copy.deepcopy(g[PRIEST])
        token.update(zone="Battlefield", controller=me["id"])
        priest.update(zone="Battlefield", controller=opp["id"])
        me["zones"]["battlefield"].append(token)
        opp["zones"]["battlefield"].append(priest)
        gui = make_gui(st)
        gui.session.handle({"t": "message", "title": "Forge", "text": REFUSAL})
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.MessageDialog)
        self.assertEqual(gui.modal.title, "Can't block like that")
        self.assertIn("can block only Priest of the Crossing", gui.modal.text)
        self.assertEqual(gui.spot_name(), "Spirit Token")


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveTests(unittest.TestCase):
    """AI 1 has Serra Angel and Priest of the Crossing wearing The Masamune; I have a Suntail Hawk (flying) and Grizzly Bears.
    Both AI flyers attack. Forge's canBlock: the Hawk may block only Priest (it must be blocked if able); the Bears can't
    block a flyer at all."""

    def test_canblock_names_priest_and_blocking_that_way_is_accepted(self):
        import card_check
        import forge_client as fc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Teysa Karlov"], ["Plains"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Me", seed=5, dev=True)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:
            s.poll()
            st = s.state
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            mv = card_check.next_move(st, [], mem) if st else None
            if mv and mv[0] == "ok":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        before = s.setups_done
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=6", "p0life=40", "p1life=40",
                 "p0battlefield=Suntail Hawk;Grizzly Bears;Plains", "p0library=Plains;Plains;Plains;Plains;Plains",
                 "p1battlefield=Serra Angel;Priest of the Crossing|Id:9101;The Masamune|AttachedTo:9101;Forest",
                 "p1library=Forest;Forest;Forest;Forest;Forest", "removesummoningsickness=true"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        end, st = time.time() + 120, {}
        while time.time() < end:
            s.poll()
            st = s.state or {}
            if st.get("input") == "InputBlock" and st.get("asking"):
                break
            if st.get("asking") and (st.get("prompt") or {}).get("ok", {}).get("enabled") and st.get("input") != "InputBlock":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.1)
        self.assertEqual(st.get("input"), "InputBlock", "never asked to block")
        cards = {c["name"]: c for c in s.me()["zones"]["battlefield"]}
        theirs = {c["name"]: c for p in st["players"] if p["id"] != st["me"] for c in p["zones"]["battlefield"]}
        attackers = sorted(c["name"] for c in theirs.values() if c.get("attacking"))
        if attackers != ["Priest of the Crossing", "Serra Angel"]:
            self.skipTest("the AI attacked with %s" % attackers)
        priest, angel = theirs["Priest of the Crossing"]["id"], theirs["Serra Angel"]["id"]
        hawk, bears = cards["Suntail Hawk"]["id"], cards["Grizzly Bears"]["id"]
        end = time.time() + 10                                     # the list comes with the question (a later snapshot)
        while time.time() < end and "canBlock" not in next(c for c in s.me()["zones"]["battlefield"] if c["id"] == hawk):
            s.poll()
            time.sleep(0.1)
        mine_now = {c["id"]: c for c in s.me()["zones"]["battlefield"]}
        self.assertEqual(mine_now[hawk].get("canBlock"), [priest])
        self.assertEqual(mine_now[bears].get("canBlock"), [])
        s.click_card(angel)                                        # the Angel is the one being blocked: the Hawk can't
        time.sleep(0.6)
        s.poll()
        s.ok()
        end, text = time.time() + 10, None
        while time.time() < end and text is None:
            s.poll()
            for info in list(s.infos):
                if "must block an attacker" in (info.get("text") or ""):
                    text = info["text"]
            time.sleep(0.1)
        self.assertIsNotNone(text, "Forge didn't refuse the empty block")
        e = why.explain(text, s.card)
        self.assertIn("can block only Priest of the Crossing: click Priest of the Crossing, then Suntail Hawk, then OK.", e.body)
        s.click_card(priest)                                       # what the explanation says
        time.sleep(0.6)
        s.poll()
        s.click_card(hawk)
        end = time.time() + 10                                     # the list follows the blocks: the Hawk now blocks Priest
        while time.time() < end and next(c for c in s.me()["zones"]["battlefield"] if c["id"] == hawk).get("canBlock") != []:
            s.poll()
            time.sleep(0.1)
        self.assertEqual(next(c for c in s.me()["zones"]["battlefield"] if c["id"] == hawk).get("canBlock"), [])
        s.ok()
        end, past = time.time() + 20, False
        while time.time() < end and not past:
            s.poll()
            st = s.state or {}
            past = st.get("input") != "InputBlock" and st.get("phase") not in ("COMBAT_DECLARE_BLOCKERS", None)
            time.sleep(0.1)
        self.assertTrue(past, "the block the explanation describes was refused")


if __name__ == "__main__":
    unittest.main()
