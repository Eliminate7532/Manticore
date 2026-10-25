# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SB3: what the soak of 19-20 Oct found (run_20261019_202002, 591 games, 1 failing game) and two soak gaps of 19 Oct.

  MustBlockTests    game 58: a creature that must block, while the block question is on an attacker it may not block
                    (The Masamune: "must be blocked if able"). Played against a stand-in that blocks the way Forge's
                    InputBlock / CombatUtil.canBlock do, from the game's real state (tests/fixtures/soak/sb3_masamune_block.json).
  RuntimeTests      card_check.load_profiles reads FORGE_RUNTIME like forge_client does (it read only the program's runtime)
  BoardGuardTests   soak_boards refuses a seat with no card Forge knows (it wrote an empty board, and every card the game
                    then named was "not found in any zone"), so the soak plays that game from turn 1 instead
  LiveMasamuneTests the same refusal from real Forge: the bot gets past it (the old bot pressed OK until the stall)
"""
import copy
import json
import os
import random
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import card_check
import soak_boards
import soak_bot
import tests.live as live

FIX = os.path.join(BASE, "tests", "fixtures", "soak", "sb3_masamune_block.json")
TOKEN, PRIEST, WOE, SOLDIER = 319, 138, 133, 322
FLYERS = {147, 138, 111}                     # Butcher of Malakir, Priest of the Crossing, Falkenrath Noble


def fixture():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


class BlockStandIn:
    """Forge's block question in game 58, as InputBlock and CombatUtil.canBlock behave (forge-gui / forge-game at fb4d809):
    clicking an attacker makes it the current one; clicking my creature that blocks the current attacker takes it off;
    clicking one that blocks nothing adds it to the current attacker only when canBlock is true - and the Spirit Token
    can't block anything but Priest of the Crossing while Priest (The Masamune: "must be blocked if able") is unblocked,
    and the Soldier Token can't block a flyer. OK is refused, with the message Forge sent, while the token blocks
    nothing; refused with "...the right ones." while it blocks another attacker."""

    def __init__(self, state=None, current=WOE):
        self.state = copy.deepcopy(state or fixture()["state"])
        self.seq = self.state["inputSeq"]
        self.blocks = {}                     # blocker -> attacker
        self.current = None
        self.set_current(current)
        self.infos = []
        self.accepted = False
        self.clicks = 0

    def attackers(self):
        return [e["card"] for e in self.state["combat"]]

    def name(self, cid):
        for _c in (c for p in self.state["players"] for c in p["zones"]["battlefield"]):
            if _c["id"] == cid:
                return _c["name"]
        return "?"

    def set_current(self, cid):
        self.current = cid
        self.state["prompt"]["message"] = ("Select creatures to block %s (%d) or select another attacker to declare "
                                           "blockers for." % (self.name(cid), cid))
        for p in self.state["players"]:
            for c in p["zones"]["battlefield"]:
                if c.get("attacking"):
                    c["highlight"] = c["id"] == cid
        self.sync()

    def sync(self):
        for e in self.state["combat"]:
            e["blockers"] = [b for b, a in self.blocks.items() if a == e["card"]]
        for c in self.state["players"][0]["zones"]["battlefield"]:
            c["blocking"] = c["id"] in self.blocks

    def can_block(self, attacker, blocker):
        if blocker in self.blocks:
            return False
        if blocker == TOKEN and PRIEST not in self.blocks.values() and attacker != PRIEST:
            return False                     # the lure rule: it must block the attacker that must be blocked
        if blocker == SOLDIER and attacker in FLYERS:
            return False
        return blocker in (TOKEN, SOLDIER)

    def click(self, cid):
        self.clicks += 1
        if cid in self.attackers():
            self.set_current(cid)
        elif self.blocks.get(cid) == self.current:
            del self.blocks[cid]
            self.sync()
        elif self.can_block(self.current, cid):
            self.blocks[cid] = self.current
            self.sync()

    def ok(self):
        if self.blocks.get(TOKEN) == PRIEST:
            self.accepted = True
            return
        which = "the right ones." if TOKEN in self.blocks else "any."
        self.infos.append({"t": "message", "title": "Forge", "text": "Spirit Token (%d) must block an attacker, but has "
                           "not been assigned to block %s" % (TOKEN, which), "_seq": self.seq})


def play(stand, bot=None, mem=None, limit=80):
    """The soak loop over the stand-in: ask the bot, do what it says; Forge's messages go into mem["infos"]."""
    bot = bot or soak_bot.SoakBot(seed=1)
    mem = {"infos": []} if mem is None else mem
    moves = []
    for _ in range(limit):
        if stand.accepted:
            break
        mem["infos"] = list(stand.infos)
        mv = bot.next_action(stand.state, [], mem)
        moves.append(mv)
        if mv is None:
            continue
        if mv[0] == "click":
            stand.click(mv[1])
        elif mv[0] == "ok":
            stand.ok()
    return moves


class MustBlockTests(unittest.TestCase):
    def test_the_real_refusal_says_what_the_fixture_says(self):
        f = fixture()
        self.assertEqual(f["refusal"]["text"], "Spirit Token (319) must block an attacker, but has not been assigned to block any.")
        self.assertEqual(f["state"]["inputSeq"], f["refusal"]["_seq"])
        self.assertIn("Woe Strider (133)", f["state"]["prompt"]["message"])
        masamune = [c for c in f["state"]["players"][1]["zones"]["battlefield"] if c["name"] == "The Masamune"][0]
        self.assertEqual(masamune["attachedTo"], PRIEST)

    def test_game_58_gets_past_the_refusal(self):
        stand = BlockStandIn()
        moves = play(stand)
        self.assertTrue(stand.accepted, "still refused after %d moves: %s" % (len(moves), moves[-12:]))
        self.assertEqual(stand.blocks.get(TOKEN), PRIEST)
        self.assertLess(len(moves), 60)

    def test_it_never_sends_more_than_one_ok_per_refusal_after_trying_everything(self):
        """Bounded: each refusal gets one plan, and the attackers run out. A stand-in where nothing works must end with the
        bot giving up (None) or pressing OK, never clicking for ever."""
        stand = BlockStandIn()
        stand.can_block = lambda a, b: False           # nothing can be blocked: every plan fails
        moves = play(stand, limit=200)
        clicks = [m for m in moves if m and m[0] == "click"]
        self.assertLessEqual(len(clicks), 2 * len(stand.attackers()) + 2)

    def test_with_the_bridges_canblock_it_goes_straight_to_the_right_attacker(self):
        state = fixture()["state"]
        token = [c for c in state["players"][0]["zones"]["battlefield"] if c["id"] == TOKEN][0]
        token["canBlock"] = [PRIEST]                    # Round BLK1's field
        stand = BlockStandIn(state)
        stand.ok()                                      # Forge's first refusal, as in the game
        moves = play(stand)
        self.assertTrue(stand.accepted)
        self.assertEqual(moves[:3], [("click", PRIEST), ("click", TOKEN), ("ok",)])

    def test_when_the_bridge_says_it_can_block_nothing_the_bot_clicks_nothing(self):
        state = fixture()["state"]
        token = [c for c in state["players"][0]["zones"]["battlefield"] if c["id"] == TOKEN][0]
        token["canBlock"] = []
        stand = BlockStandIn(state)
        stand.ok()
        bot, mem = soak_bot.SoakBot(seed=1), {"infos": list(stand.infos)}
        self.assertIsNone(bot._required_block_move(stand.state, stand.state["players"][0], stand.state["prompt"], mem))

    def test_a_creature_blocking_the_wrong_attacker_comes_off_it_first(self):
        stand = BlockStandIn(current=PRIEST)
        stand.can_block = lambda a, b: b == TOKEN and TOKEN not in stand.blocks   # let it block anything, once
        stand.click(TOKEN)                              # on Priest... then moved by hand to Woe Strider
        del stand.blocks[TOKEN]
        stand.set_current(WOE)
        stand.click(TOKEN)
        self.assertEqual(stand.blocks.get(TOKEN), WOE)
        stand.ok()
        self.assertIn("the right ones.", stand.infos[-1]["text"])
        bot = soak_bot.SoakBot(seed=1)
        mem = {"infos": list(stand.infos)}
        first = [bot.next_action(stand.state, [], mem) for _ in range(2)]
        self.assertEqual(first, [("click", WOE), ("click", TOKEN)])     # off Woe Strider: click it there again
        for mv in first:
            stand.click(mv[1])
        self.assertNotIn(TOKEN, stand.blocks)

    def test_an_attacker_the_message_names_is_tried_first(self):
        state = fixture()["state"]
        stand = BlockStandIn(state)
        mem = {"infos": [{"t": "message", "text": "Spirit Token (319) must still block Priest of the Crossing (138).",
                          "_seq": stand.seq}]}
        bot = soak_bot.SoakBot(seed=1)
        self.assertEqual([bot.next_action(stand.state, [], mem) for _ in range(2)], [("click", PRIEST), ("click", TOKEN)])

    def test_a_new_question_drops_the_plan(self):
        stand = BlockStandIn()
        stand.ok()
        bot, mem = soak_bot.SoakBot(seed=1), {"infos": list(stand.infos)}
        bot.next_action(stand.state, [], mem)          # the current attacker first: one click on the token
        bot.next_action(stand.state, [], mem)          # the same refusal: OK again (no click)
        stand.ok()
        mem["infos"] = list(stand.infos)
        mv = bot.next_action(stand.state, [], mem)      # second refusal: another attacker, with the token queued
        self.assertEqual(mv[0], "click")
        self.assertTrue(mem["soak_block_queue"])
        later = copy.deepcopy(stand.state)
        later["inputSeq"] = stand.seq + 1
        later["input"] = "InputPassPriority"
        later["prompt"] = {"message": "Priority: Soak - stack empty", "ok": {"label": "OK", "enabled": True}}
        bot.next_action(later, [], mem)
        self.assertEqual(mem["soak_block_queue"], [])
        self.assertEqual(mem["soak_must_block"], {})


class RuntimeTests(unittest.TestCase):
    def make_runtime(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "res", "cardsfolder", "z")
        os.makedirs(folder)
        with open(os.path.join(folder, "zzz_test_bear.txt"), "w", encoding="utf-8") as f:
            f.write("Name:Zzz Test Bear\nManaCost:1 G\nTypes:Creature Bear\nPT:2/2\n")
        deck = os.path.join(root, "deck.txt")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("1 Zzz Test Bear\n")
        return root, deck

    def test_load_profiles_reads_forge_runtime(self):
        root, deck = self.make_runtime()
        old = os.environ.get("FORGE_RUNTIME")
        os.environ["FORGE_RUNTIME"] = root
        try:
            got = card_check.load_profiles(deck)
        finally:
            if old is None:
                os.environ.pop("FORGE_RUNTIME", None)
            else:
                os.environ["FORGE_RUNTIME"] = old
        self.assertEqual([(p.name, p.found) for p in got], [("Zzz Test Bear", True)])

    def test_an_explicit_runtime_still_wins(self):
        root, deck = self.make_runtime()
        old = os.environ.get("FORGE_RUNTIME")
        os.environ["FORGE_RUNTIME"] = tempfile.mkdtemp()        # an empty one
        try:
            got = card_check.load_profiles(deck, runtime=root)
        finally:
            if old is None:
                os.environ.pop("FORGE_RUNTIME", None)
            else:
                os.environ["FORGE_RUNTIME"] = old
        self.assertTrue(got[0].found)


class BoardGuardTests(unittest.TestCase):
    def test_a_seat_with_no_known_card_is_refused(self):
        empty = tempfile.mkdtemp()                                # a runtime with no card scripts at all
        deck = os.path.join(BASE, "sample_decks", "aristocrats_teysa.txt")
        with self.assertRaises(ValueError) as cm:
            soak_boards.board_lines([deck, deck], random.Random(1), runtime=empty)
        self.assertIn("seat 1", str(cm.exception))

    @unittest.skipIf(live.live_problem(), "needs forge_runtime's card scripts")
    def test_a_real_deck_still_gets_a_board(self):
        deck = os.path.join(BASE, "sample_decks", "aristocrats_teysa.txt")
        lines, placed = soak_boards.board_lines([deck, deck], random.Random(1))
        self.assertTrue(placed[0] and placed[1])
        self.assertTrue(any(l.startswith("p0library=") and len(l) > 20 for l in lines))


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveMasamuneTests(unittest.TestCase):
    """Real Forge: AI 1 attacks with Serra Angel and Priest of the Crossing wearing The Masamune; my only creature is a
    flyer. The test makes Serra Angel the current attacker and presses OK, as the bot did with Woe Strider in game 58;
    Forge refuses ("... must block an attacker ..."). Then the bot plays: it must get Forge past the block question."""

    def test_the_bot_gets_past_a_masamune_refusal(self):
        import forge_client as fc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Teysa Karlov"], ["Plains"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Soak", seed=5, dev=True)
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
                 "p0battlefield=Suntail Hawk;Plains", "p0library=Plains;Plains;Plains;Plains;Plains",
                 "p1battlefield=Serra Angel;Priest of the Crossing|Id:9101;The Masamune|AttachedTo:9101;Forest",
                 "p1library=Forest;Forest;Forest;Forest;Forest", "removesummoningsickness=true"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        bot, bmem, seen, forced, refused, past = soak_bot.SoakBot(seed=1), {"infos": []}, 0, False, False, False
        end = time.time() + 150
        while time.time() < end and not s.game_over and not past:
            s.poll()
            st = s.state or {}
            while len(s.infos) > seen:
                info = dict(s.infos[seen])
                info["_seq"] = st.get("inputSeq")
                bmem["infos"].append(info)
                refused = refused or "must block an attacker" in (info.get("text") or "")
                seen += 1
            if st.get("input") == "InputBlock" and not forced:
                cards = [c for p in st["players"] for c in p["zones"]["battlefield"]]
                attackers = [c for c in cards if c.get("attacking")]
                if not any(c["name"] == "Priest of the Crossing" for c in attackers) or len(attackers) < 2:
                    self.skipTest("the AI didn't attack with both flyers (it attacked with %s)" % [c["name"] for c in attackers])
                angel = next(c for c in attackers if c["name"] == "Serra Angel")
                s.click_card(angel["id"])                 # the question on the attacker the Hawk may NOT block
                time.sleep(0.6)
                s.poll()
                s.ok()                                    # no blockers: Forge refuses (the Hawk must block Priest)
                time.sleep(1.5)
                forced = True
                continue
            if forced:
                if st.get("input") != "InputBlock" and st.get("phase") != "COMBAT_DECLARE_BLOCKERS":
                    past = True
                    break
                if st.get("asking") is False:
                    time.sleep(0.1)
                    continue
                mv = bot.next_action(st, list(s.requests), bmem)
                if mv and mv[0] == "click":
                    s.click_card(mv[1])
                elif mv and mv[0] == "ok":
                    s.ok()
                elif mv and mv[0] == "answer":
                    s.answer(mv[1], mv[2])
                time.sleep(0.5)
            else:
                mv = bot.next_action(st, list(s.requests), bmem) if st.get("asking") else None
                if mv and mv[0] == "ok" and st.get("input") != "InputBlock":
                    s.ok()
                elif mv and mv[0] == "answer":
                    s.answer(mv[1], mv[2])
                time.sleep(0.3)
        self.assertTrue(forced, "the AI never attacked")
        self.assertTrue(refused, "Forge didn't refuse the empty block")
        self.assertTrue(past, "the bot never got Forge past the block question")


if __name__ == "__main__":
    unittest.main()
