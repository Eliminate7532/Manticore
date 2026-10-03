# SPDX-License-Identifier: GPL-3.0-or-later
"""
Live tests: start the real Forge engine (Java) and play against its AI through ForgeSession.

Skipped automatically when Java or the Forge runtime (python setup_forge.py) is not installed.
They take a minute or two: Forge needs 10-20 seconds to read its card scripts on every start.
    python -m unittest tests.test_forge_live -v
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import tests.live as live
from tests.forge_bot import Bot

PROBLEM = live.live_problem()
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(BASE, "sample_decks", "kinnan_nbc_moxfield_export.txt")


def write_decks(directory):
    from deck_loader import load_deck
    commanders, deck = load_deck(SAMPLE)
    kinnan = fc.write_deck_file(os.path.join(directory, "kinnan.dck"), commanders, deck, "Kinnan")
    gems = fc.write_deck_file(os.path.join(directory, "gems.dck"), ["Kinnan, Bonder Prodigy"],
                              ["Gemstone Caverns"] * 25 + ["Forest"] * 74, "Gems")
    return kinnan, gems


def wait_for(session, cond, seconds):
    end = time.time() + seconds
    while time.time() < end:
        session.poll()
        if cond():
            return True
        if session.exited or session.fatal:
            return False
        time.sleep(0.03)
    return False


@unittest.skipIf(PROBLEM, f"Forge is not ready here: {PROBLEM}")
class LiveForgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()          # the same step the game takes at start-up: an OLD bridge jar left in forge_runtime/ made this test
                                  # fail on Karl's PC ("no colourless mana is reported") although the current bridge is fine
        cls.tmp = tempfile.TemporaryDirectory()
        cls.kinnan, cls.gems = write_decks(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, mine, opp, seed, dev=False):
        s = fc.ForgeSession(mine, [opp], name="Tester", seed=seed, dev=dev)
        s.stderr_path = os.path.join(self.tmp.name, f"engine_{seed}.log")
        s.start()
        self.addCleanup(s.close)
        return s

    def reach_priority(self, s, seconds=150):
        """Get through the coin toss and the opening hand to the first moment the player holds priority (only then is a dev setup safe)."""
        import card_check
        mem = {}
        end = time.time() + seconds
        while time.time() < end:
            s.poll()
            self.assertFalse(s.exited or s.fatal, s.fatal or "Forge stopped while starting")
            st = s.state
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else list(range(max(req.get("min", 1), 1))))
            elif st and st["prompt"]["message"].startswith("Priority:"):
                return
            elif st:
                move = card_check.next_move(st, [], mem)
                if move and move[0] == "ok":
                    s.ok()
                    time.sleep(0.4)
            time.sleep(0.05)
        self.fail("the game never reached the first turn")

    def test_a_scripted_player_can_play_the_first_turns(self):
        s = self.start(self.kinnan, self.kinnan, seed=3)
        bot = Bot(s)
        ok = bot.run(150, until=lambda sess: (sess.state or {}).get("turn", 0) >= 5)
        self.assertIsNone(s.fatal, s.fatal)
        self.assertTrue(ok, f"never reached turn 5; last actions: {bot.actions[-8:]}")
        me = s.me()
        self.assertGreater(len(me["zones"]["battlefield"]), 0, "the bot never got a permanent onto the battlefield")
        self.assertTrue(any(l["type"] == "LAND" for l in s.log), "no land was played")

    def test_snapshot_shows_life_command_zone_and_hides_the_opponents_hand(self):
        s = self.start(self.kinnan, self.kinnan, seed=4)
        self.assertTrue(wait_for(s, lambda: s.me() and s.me()["zones"]["command"] and s.opponents() and s.opponents()[0]["libraryCount"] > 80, 90))
        me, opp = s.me(), s.opponents()[0]
        self.assertEqual(me["life"], 40)
        self.assertEqual(len(me["zones"]["command"]), 1)
        self.assertEqual(me["zones"]["command"][0]["name"], "Kinnan, Bonder Prodigy")
        self.assertNotIn("hand", opp["zones"])            # the opponent's hand is hidden from us
        self.assertGreater(opp["libraryCount"], 80)

    def test_gemstone_caverns_is_offered_before_turn_one_when_going_second(self):
        """The bug that started all this: the old table could not play Gemstone Caverns before the first upkeep."""
        for seed in (1, 2, 3, 4, 5, 6):
            s = self.start(self.gems, self.kinnan, seed)
            offered = self.play_opening(s)
            if offered is None:
                continue                                    # we ended up on the play: Caverns does nothing there
            self.assertTrue(offered, "Forge never offered the opening-hand action")
            bf = s.me()["zones"]["battlefield"]
            caverns = [c for c in bf if c["name"] == "Gemstone Caverns"]
            self.assertTrue(caverns, "Gemstone Caverns did not begin the game on the battlefield")
            self.assertEqual(caverns[0].get("counters", {}).get("LUCK"), 1)
            return
        self.skipTest("never ended up going second in six tries")

    def test_every_card_of_the_sample_deck_is_known_to_forge(self):
        """Reported from Karl's PC: Forge's log said it could not find the deck's two-faced cards (asked for as 'A // B'),
        so they were silently missing from the deck. Hand + library must be all 99 cards, and the log must be clean."""
        s = self.start(self.kinnan, self.kinnan, seed=8)
        self.assertTrue(wait_for(s, lambda: s.me() and s.me()["libraryCount"] > 0 and s.opponents()
                                 and s.opponents()[0]["libraryCount"] > 0, 90))
        for p in [s.me()] + s.opponents():
            self.assertEqual(p["handCount"] + p["libraryCount"], 99, f"{p['name']} is missing cards")
        s.close()
        with open(s.stderr_path, "r", encoding="utf-8", errors="replace") as f:
            log = f.read()
        self.assertNotIn("unsupported card was requested", log)
        self.assertNotIn("could not find this card", log)

    def test_fetchland_search_arrives_as_a_list_of_library_cards(self):
        """Reported on Karl's PC: after cracking a fetchland the game said 'Select a card from your library' but showed no
        library to pick from. Forge must be asked (bridge preference) to send a list, and answering it must fetch the land.
        Round 14: the board is set up with the developer 'setup' command (a Wooded Foothills in hand, my turn, main phase), so the test no
        longer depends on drawing the land, on who goes first or on how long the AI's turns take - it used to fail now and then for those."""
        fetch = fc.write_deck_file(os.path.join(self.tmp.name, "fetch.dck"), ["Kinnan, Bonder Prodigy"],
                                   ["Wooded Foothills"] * 40 + ["Mountain"] * 30 + ["Forest"] * 29, "Fetch")
        s = self.start(fetch, self.kinnan, seed=3, dev=True)
        self.reach_priority(s)
        s.setup(["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Wooded Foothills", "humanbattlefield=Forest", "aihand=", "aibattlefield=Forest", "removesummoningsickness=true",
                 "humanlibrary=" + ";".join(["Forest"] * 8 + ["Mountain"] * 8 + ["Island"] * 4)])     # a setup EMPTIES a library it does not list

        def names(zone):
            return [c["name"] for c in s.me()["zones"][zone]]

        self.assertTrue(wait_for(s, lambda: names("hand") == ["Wooded Foothills"], 20), "the setup never put the land in my hand")
        time.sleep(0.6)
        s.poll()
        s.click_card(s.me()["zones"]["hand"][0]["id"])                                       # play it
        self.assertTrue(wait_for(s, lambda: "Wooded Foothills" in names("battlefield"), 20), "the land was not played")
        time.sleep(0.6)
        s.poll()
        s.click_card(next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Wooded Foothills")["id"])      # crack it
        self.assertTrue(wait_for(s, lambda: "pay 1 life" in s.state["prompt"]["message"], 20), "Forge did not ask to pay 1 life")
        s.ok()                                                                               # Yes
        req = None
        end = time.time() + 30
        while time.time() < end and req is None:
            s.poll()
            if s.requests:
                req = s.requests[0]
            elif "Stack: 1" in s.state["prompt"]["message"] and s.state["prompt"]["ok"]["enabled"]:
                s.ok()                                                                       # let the ability resolve; Forge then asks which land
                time.sleep(0.3)
            time.sleep(0.05)
        self.assertIsNotNone(req, "the fetch search list never arrived; the prompt said: %r" % s.state["prompt"]["message"])
        self.assertIn("library", req.get("title", "").lower())
        self.assertIn(req["kind"], ("choose", "choose_optional"))
        items = req["items"]
        self.assertEqual(len(items), 16, "every Forest and Mountain in the library should be listed (and no Island)")
        self.assertTrue(all(i["kind"] == "card" and i["card"]["name"] in ("Forest", "Mountain") for i in items))
        s.answer(req, [next(n for n, i in enumerate(items) if i["card"]["name"] == "Forest")])
        self.assertTrue(wait_for(s, lambda: names("battlefield").count("Forest") == 2, 20), "the fetched Forest did not arrive")
        self.assertIsNone(s.fatal, s.fatal)
        self.assertIn("Wooded Foothills", names("graveyard"))

    def test_the_developer_setup_command_is_ignored_unless_the_game_was_started_in_dev_mode(self):
        """Round 14: 'setup' rewrites the board (life, hands, battlefields). Only card_check, the tests and replays of reports that used it start
        the bridge with --dev; in a normal game the command must change nothing (a crafted or stray line cannot give anyone a free win)."""
        s = self.start(self.kinnan, self.kinnan, seed=5)
        self.reach_priority(s)
        before = [(p["name"], p["life"]) for p in s.state["players"]]
        s.setup(["humanlife=1", "ailife=1", "activeplayer=human", "activephase=MAIN1", "turn=9", "humanhand=Sol Ring", "aihand=Sol Ring"])
        end = time.time() + 4
        while time.time() < end:
            s.poll()
            time.sleep(0.05)
        self.assertEqual([(p["name"], p["life"]) for p in s.state["players"]], before)
        self.assertNotEqual(s.me()["handCount"], 1)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertIn("setup ignored", f.read())

    def test_the_first_mulligan_is_free_and_the_next_ones_put_cards_on_the_bottom(self):
        """Commander: mulligan once = a fresh seven (Forge only does that with 3+ players; forge_bridge.jar patches it for 1v1);
        mulligan twice = seven, one back on the bottom = six; a third = five."""
        s = self.start(self.kinnan, self.kinnan, seed=11)
        sizes, prompts = [], []
        taken, last, since, t0 = 0, None, time.time(), time.time()
        while time.time() - t0 < 120 and not (s.exited or s.fatal):
            s.poll()
            st = s.state
            if not st:
                time.sleep(0.05)
                continue
            p, msg = st["prompt"], st["prompt"]["message"]
            key = (msg, len(s.me()["zones"]["hand"]))
            if key != last:
                last, since = key, time.time()
            if time.time() - since > 0.6:
                since = time.time() + 5
                if "coin toss" in msg:
                    s.ok()
                elif s.is_mulligan_prompt():
                    sizes.append(s.me()["handCount"])
                    if taken < 3:
                        taken += 1
                        s.cancel()
                    else:
                        break
                elif msg.startswith("Return "):
                    n = int(msg.split()[1])
                    prompts.append(n)
                    for c in s.me()["zones"]["hand"][:n]:
                        s.click_card(c["id"])
                        time.sleep(0.3)
                    s.ok()
            time.sleep(0.05)
        self.assertEqual(sizes, [7, 7, 6, 5], f"hand sizes at each keep-or-mulligan question (prompts: {prompts})")
        self.assertEqual(prompts, [0, 1, 2], "cards returned to the bottom after mulligan 1, 2, 3")

    def test_gemstone_caverns_is_still_offered_after_a_mulligan_when_going_second(self):
        """Reported by Karl: after a mulligan the game did not let him use Gemstone Caverns before turn 1."""
        for seed in (1, 2, 3, 4, 5, 6):
            s = self.start(self.gems, self.kinnan, seed)
            offered = self.play_opening(s, mulligans=1)
            if offered is None:
                continue                                    # we ended up on the play: Caverns does nothing there
            self.assertTrue(offered, "Forge never offered the opening-hand action after the mulligan")
            caverns = [c for c in s.me()["zones"]["battlefield"] if c["name"] == "Gemstone Caverns"]
            self.assertTrue(caverns, "Gemstone Caverns did not begin the game on the battlefield")
            return
        self.skipTest("never ended up going second in six tries")

    def test_floating_colourless_mana_is_reported_and_pays_for_the_monolith_untap(self):
        """Karl's loop: Kinnan + Basalt Monolith. Tapping the Monolith (CCC, +C from Kinnan) must show C:4 floating (Forge keys colourless
        under a different number than the other colours), clicking it must pay for '{3}: Untap' one at a time, and the Monolith untaps."""
        mono = fc.write_deck_file(os.path.join(self.tmp.name, "mono.dck"), ["Kinnan, Bonder Prodigy"],
                                  ["Basalt Monolith"] * 33 + ["Forest"] * 33 + ["Island"] * 33, "Mono")
        s = self.start(mono, mono, seed=1)
        bot = Bot(s, cast=True)

        def mine(zone, name):
            return [c for c in s.me()["zones"].get(zone, []) if c["name"] == name]

        stage, t0 = "warm", time.time()
        while time.time() - t0 < 170 and not (s.exited or s.fatal):
            s.poll()
            st = s.state
            if not st or not s.me():
                time.sleep(0.05)
                continue
            msg = st["prompt"]["message"]
            me_turn = st.get("activePlayer") == st.get("me")
            open_main = "Main phase" in msg and "Stack: Empty" in msg and me_turn
            if s.requests or s.is_mulligan_prompt() or "coin toss" in msg or "Pay" in msg:
                bot.step()
                time.sleep(0.03)
                continue
            kin, monos = mine("battlefield", "Kinnan, Bonder Prodigy"), mine("battlefield", "Basalt Monolith")
            if stage == "warm":
                if open_main:
                    m, cmd = s.me(), s.me()["zones"]["command"]
                    lands = [c for c in m["zones"]["hand"] if c.get("isLand")]
                    if lands and m["landsPlayed"] < m["maxLandPlay"]:
                        s.click_card(lands[0]["id"])
                        time.sleep(0.4)
                        continue
                    if not kin and cmd and (cmd[0].get("weak") or cmd[0].get("selectable")):
                        s.click_card(cmd[0]["id"])
                        time.sleep(0.5)
                        continue
                    hand = [c for c in m["zones"]["hand"] if c["name"] == "Basalt Monolith" and (c.get("weak") or c.get("selectable"))]
                    if kin and not monos and hand:
                        s.click_card(hand[0]["id"])
                        time.sleep(0.5)
                        continue
                    if kin and monos and not monos[0]["tapped"]:
                        stage = "tap"
                        continue
                    s.ok()
                else:
                    bot.step()
                time.sleep(0.05)
                continue
            if stage == "tap":
                s.click_card(monos[0]["id"])
                self.assertTrue(wait_for(s, lambda: s.requests, 10), "the Monolith should offer its abilities")
                s.answer(s.requests[0], [0])                                  # {T}: Add {C}{C}{C}
                self.assertTrue(wait_for(s, lambda: (s.me().get("manaPool") or {}).get("C"), 10), "no colourless mana is reported")
                self.assertEqual(s.me()["manaPool"].get("C"), 4, s.me()["manaPool"])
                stage = "untap"
                continue
            if stage == "untap":
                s.click_card(monos[0]["id"])
                self.assertTrue(wait_for(s, lambda: "Untap" in s.state["prompt"]["message"] or s.requests, 10))
                if s.requests:
                    s.answer(s.requests[0], [1])                              # {3}: Untap this artifact
                self.assertTrue(wait_for(s, lambda: "Pay Mana Cost" in s.state["prompt"]["message"], 10))
                for left in (3, 2, 1):
                    s.use_mana("C")
                    self.assertTrue(wait_for(s, lambda: (s.me().get("manaPool") or {}).get("C") == left, 10),
                                    f"one click should spend one C (expected {left} left, pool {s.me().get('manaPool')})")
                self.assertTrue(wait_for(s, lambda: s.state.get("stack"), 10), "the untap should be on the stack once paid")
                s.ok()
                self.assertTrue(wait_for(s, lambda: mine("battlefield", "Basalt Monolith")[0]["tapped"] is False, 10),
                                "the Monolith should be untapped")
                return
        self.skipTest("never got Kinnan and the Monolith out together with this seed")

    # ---- passing for me (round 10) ----------------------------------------------------------------

    def to_my_main1(self, s, want=lambda sess: True):
        """Keep the opening hand and pass until it is my precombat main phase with `want` true. Answers plain questions with the first choice."""
        last = 0
        end = time.time() + 120
        while time.time() < end and not (s.exited or s.fatal):
            s.poll()
            time.sleep(0.03)
            st = s.state
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else [0])
                continue
            if not (st and s.me() and st["prompt"].get("ok", {}).get("enabled")) or time.time() - last < 0.6:
                continue
            last = time.time()
            if s.is_mulligan_prompt():
                s.ok()
            elif st.get("activePlayer") == st.get("me") and st["phase"] == "MAIN1" and st["turn"] >= 1 and want(s):
                return True
            else:
                s.ok()
        return False

    def test_skip_to_my_next_turn_passes_the_opponents_turns_by_itself(self):
        two = fc.write_deck_file(os.path.join(self.tmp.name, "islands.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Islands")
        s = self.start(two, self.gems, seed=5)
        self.assertTrue(self.to_my_main1(s), "never reached my first main phase")
        first_turn = s.state["turn"]
        self.assertIn("yield", s.state, "the bridge does not send its yield state")
        self.assertNotIn("mode", s.state["yield"])
        s.yield_until("UPKEEP")
        seen_mode = wait_for(s, lambda: s.state["yield"].get("mode") == "until", 10)
        self.assertTrue(seen_mode, "Forge never reported the skip")
        end = time.time() + 60
        while time.time() < end:
            s.poll()
            time.sleep(0.03)
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else [0])
            elif "to discard" in s.state["prompt"]["message"] and s.me()["zones"]["hand"]:
                s.click_card(s.me()["zones"]["hand"][0]["id"])
                time.sleep(0.2)
                s.poll()
                s.ok()
            if s.state["turn"] > first_turn + 1 and s.state.get("activePlayer") == s.state.get("me") and s.state["prompt"]["ok"]["enabled"]:
                break
        self.assertGreater(s.state["turn"], first_turn + 1, "the skip did not get past the opponent's turn")
        self.assertEqual(s.state.get("activePlayer"), s.state.get("me"))
        self.assertNotIn("mode", s.state["yield"], "the skip should be over once my turn came round")

    def test_an_opponents_attack_ends_a_skip_to_my_next_turn(self):
        """Round 12 (was unverified in round 10): Forge's YIELD_INTERRUPT_ON_ATTACKERS stops the skip when a creature attacks me."""
        me_deck = fc.write_deck_file(os.path.join(self.tmp.name, "islands2.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Islands")
        bears = fc.write_deck_file(os.path.join(self.tmp.name, "bears.dck"), ["Kinnan, Bonder Prodigy"],
                                   ["Forest"] * 45 + ["Grizzly Bears"] * 54, "Bears")
        s = self.start(me_deck, bears, seed=3)
        self.assertTrue(self.to_my_main1(s), "never reached my first main phase")
        mine = lambda: s.state.get("activePlayer") == s.state.get("me")
        end = time.time() + 120
        last = 0
        stopped_by_attack = None
        skipping = False
        while time.time() < end and stopped_by_attack is None:
            s.poll()
            time.sleep(0.03)
            st = s.state
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else [0])
                continue
            if "to discard" in st["prompt"]["message"] and time.time() - last > 0.5:
                last = time.time()
                s.click_card(s.me()["zones"]["hand"][0]["id"])
                time.sleep(0.2)
                s.poll()
                s.ok()
                continue
            if not st["prompt"]["ok"]["enabled"] or time.time() - last < 0.6:
                continue
            last = time.time()
            if not mine() and st["phase"] == "COMBAT_DECLARE_ATTACKERS" and any(a.get("defender") == "p%d" % st["me"] for a in st.get("combat", [])):
                stopped_by_attack = st
            elif not mine() and skipping and "mode" not in st["yield"]:
                s.yield_until("UPKEEP")                                   # an opponent's spell ended the skip: start it again
            elif mine() and st["phase"] == "MAIN1" and st["turn"] >= 1:
                s.yield_until("UPKEEP")
                skipping = True
            else:
                s.ok()
        self.assertIsNotNone(stopped_by_attack, "the skip never gave me priority when a creature attacked me")
        self.assertNotIn("mode", stopped_by_attack["yield"], "the skip should have ended when the attack was declared")

    def test_a_bug_report_can_be_replayed_against_the_same_game(self):
        """Round 12: play a short game (Kinnan deck against the Kinnan AI), write the bug report zip, replay it from the zip and get the same
        board and the same game log. This is the whole point of recording the seed and every click."""
        import reporting
        import replay
        from deck_loader import load_deck
        commanders, cards = load_deck(SAMPLE)
        folder = os.path.join(self.tmp.name, "reportfolder")
        deck_dir = os.path.join(folder, "forge_decks")
        mine = fc.write_deck_file(os.path.join(deck_dir, "player.dck"), commanders, cards, "Player")
        theirs = fc.write_deck_file(os.path.join(deck_dir, "opponent1.dck"), commanders, cards, "Opponent 1")
        s = fc.ForgeSession(mine, [theirs], name="Karl", seed=None)                 # no seed given: the session must pick one
        s.stderr_path = os.path.join(self.tmp.name, "engine_replay.log")
        s.start()
        self.addCleanup(s.close)
        self.assertIsNotNone(s.seed, "a game must always get a seed")
        last, sent, quiet_since, seen = 0, 0, time.time(), s.state_version
        end = time.time() + 240                       # Round 14: the engine's start-up used to eat into the 90 s the scripted game had (slow disk, busy PC)
        playing_since = None
        while time.time() < end:
            s.poll()
            time.sleep(0.03)
            if playing_since is None and s.state and s.me():
                playing_since = time.time()
                end = min(end, playing_since + 90)    # 90 seconds of play once the first board is on screen
            if s.state_version != seen:
                seen, quiet_since = s.state_version, time.time()
            st = s.state
            if sent >= 25:
                if time.time() - quiet_since > 1.5:
                    break
                continue
            if not (st and s.me()):
                continue
            if "to discard" in st["prompt"]["message"] and time.time() - last > 0.5:
                last = time.time()
                s.click_card(s.me()["zones"]["hand"][0]["id"])
                time.sleep(0.2)
                s.poll()
                s.ok()
                sent += 2
                continue
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else [0])
                sent += 1
                continue
            if st["prompt"].get("selecting") and not st["prompt"]["ok"]["enabled"] and time.time() - last > 0.5:      # any other "pick a card" question
                pick = [c for p in st["players"] for z in p["zones"].values() for c in z if c.get("selectable")]
                if pick:
                    last = time.time()
                    s.click_card(pick[0]["id"])
                    sent += 1
                continue
            if not st["prompt"]["ok"]["enabled"] or time.time() - last < 0.5:
                continue
            last = time.time()
            me = s.me()
            land = next((c for c in me["zones"]["hand"] if c.get("isLand")), None)
            if land and st.get("activePlayer") == st["me"] and st["phase"] == "MAIN1" and me["landsPlayed"] < me["maxLandPlay"] and not s.is_mulligan_prompt():
                s.click_card(land["id"])
            else:
                s.ok()
            sent += 1
        time.sleep(0.5)
        s.poll()
        self.assertGreaterEqual(len(s.sent_all), 20, f"the scripted game sent too few clicks to be a test (exited={s.exited}, fatal={s.fatal}, "
                                                     f"prompt={(s.state or {}).get('prompt', {}).get('message')!r}, requests={list(s.requests)})")
        original_state = s.state
        original_log = [f"[{e['type']}] {e['text']}" for e in s.log]
        clicks = list(s.sent_all)
        path = reporting.build_report({"name": "Tester", "happened": "test", "expected": "", "seed": s.seed}, original_state, original_log,
                                      [(t - clicks[0][0], c) for t, c in clicks], None, folder=folder, dest_dir=self.tmp.name)
        s.close()
        report = replay.load_report(path)
        self.assertEqual(report.seed, s.seed)
        self.assertTrue(report.complete)
        self.assertEqual(len(report.commands), len(clicks))
        result = replay.replay(report)
        self.assertIsNone(result["diverged"], result["diverged"])
        self.assertEqual(result["differences"], [], "the replayed board differs from the original")
        self.assertEqual(result["log"][:len(original_log)][-40:], original_log[-40:], "the replayed game log differs from the original")

    def test_a_keyword_gained_until_end_of_turn_shows_up_in_the_snapshot(self):
        """Round 13: the bridge sends each permanent's current keywords. Jump ('target creature gains flying until end of turn') on a vanilla creature."""
        deck = fc.write_deck_file(os.path.join(self.tmp.name, "jump.dck"), ["Kinnan, Bonder Prodigy"],
                                  ["Island"] * 30 + ["Merfolk of the Pearl Trident"] * 30 + ["Jump"] * 39, "Jump")
        s = self.start(deck, self.gems, seed=3)
        hand = lambda n: [c for c in s.me()["zones"]["hand"] if c["name"] == n]
        field = lambda n: [c for c in s.me()["zones"]["battlefield"] if c["name"] == n]

        def pump(n=20):
            for _ in range(n):
                s.poll()
                time.sleep(0.04)
        self.assertTrue(self.to_my_main1(s, lambda sess: hand("Merfolk of the Pearl Trident") and hand("Island")), "no Merfolk and Island")
        s.click_card(hand("Island")[0]["id"])
        pump()
        s.click_card(hand("Merfolk of the Pearl Trident")[0]["id"])
        pump()
        s.click_card(field("Island")[0]["id"])                            # pay {U}
        pump()
        s.ok()                                                             # let it resolve
        pump(30)
        self.assertTrue(field("Merfolk of the Pearl Trident"), "the Merfolk did not resolve")
        self.assertFalse(field("Merfolk of the Pearl Trident")[0].get("keywords"), "a vanilla creature should report no keywords")
        first_turn = s.state["turn"]
        self.assertTrue(self.to_my_main1(s, lambda sess: sess.state["turn"] > first_turn and hand("Island") and hand("Jump")), "no second turn with Jump")
        s.click_card(hand("Island")[0]["id"])
        pump()
        s.click_card(hand("Jump")[0]["id"])
        pump()
        s.click_card(field("Merfolk of the Pearl Trident")[0]["id"])       # the target
        pump()
        s.click_card(next(c for c in field("Island") if not c.get("tapped"))["id"])
        pump()
        s.ok()
        pump(30)
        self.assertEqual(field("Merfolk of the Pearl Trident")[0].get("keywords"), ["Flying"])

    def test_always_pass_lets_the_same_ability_resolve_without_asking(self):
        bauble = fc.write_deck_file(os.path.join(self.tmp.name, "baubles.dck"), ["Kinnan, Bonder Prodigy"],
                                    ["Mishra's Bauble"] * 40 + ["Island"] * 59, "Baubles")
        s = self.start(bauble, self.gems, seed=3)
        count = lambda sess: sum(1 for c in sess.me()["zones"]["hand"] if c["name"] == "Mishra's Bauble")
        self.assertTrue(self.to_my_main1(s, lambda sess: count(sess) >= 2), "never got two Baubles in hand")

        def pump(n=25):
            for _ in range(n):
                s.poll()
                time.sleep(0.04)

        def activate():
            for c in [c for c in s.me()["zones"]["hand"] if c["name"] == "Mishra's Bauble"][:1]:
                s.click_card(c["id"])
                pump(15)
                s.ok()
                pump(20)
            bauble_card = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Mishra's Bauble" and not c.get("tapped"))
            s.click_card(bauble_card["id"])
            pump(20)
            if "Select target player" in s.state["prompt"]["message"]:
                s.click_player(s.state["me"])
                pump(20)
            if s.state["prompt"]["message"].startswith("Sacrifice"):
                s.ok()
                pump(30)

        activate()
        self.assertEqual(len(s.state["stack"]), 1, "the first activation should wait on the stack for me")
        item = s.state["stack"][0]
        self.assertTrue(item["ability"])
        self.assertTrue(item["key"])
        self.assertNotIn("autoYield", item)
        s.auto_yield(item["key"], True)
        self.assertTrue(wait_for(s, lambda: not s.state["stack"], 10), "'always pass' should let the waiting ability resolve")
        self.assertTrue(s.state["yield"]["autoYields"], "Forge did not report the rule")
        activate()
        self.assertEqual(s.state["stack"], [], "the second activation should have resolved by itself")
        s.auto_yield(s.state["yield"]["autoYields"][0], False)
        self.assertTrue(wait_for(s, lambda: not s.state["yield"]["autoYields"], 10), "the rule should be gone after 'stop'")

    def test_chrome_mox_reports_the_card_imprinted_on_it(self):
        """Round 11: the snapshot says which card is imprinted (and its colours), so the table can show what the Mox can make."""
        deck = fc.write_deck_file(os.path.join(self.tmp.name, "moxes.dck"), ["Kinnan, Bonder Prodigy"],
                                  ["Chrome Mox"] * 40 + ["Counterspell"] * 30 + ["Island"] * 29, "Moxes")
        s = self.start(deck, self.gems, seed=3)
        have = lambda sess, n: sum(1 for c in sess.me()["zones"]["hand"] if c["name"] == n)
        self.assertTrue(self.to_my_main1(s, lambda sess: have(sess, "Chrome Mox") and have(sess, "Counterspell")), "never got both cards")
        mine = lambda zone: s.me()["zones"][zone]
        s.click_card(next(c for c in mine("hand") if c["name"] == "Chrome Mox")["id"])
        imprinted = lambda: any(c["name"] == "Chrome Mox" and c.get("imprinted") for c in mine("battlefield"))
        end = time.time() + 30
        while time.time() < end and not imprinted():
            s.poll()
            time.sleep(0.05)
            prompt = s.state["prompt"]
            if s.requests:
                req = s.requests[0]
                s.answer(req, True if req["kind"] == "confirm" else [0])
            elif prompt["message"].startswith("Use triggered ability of Chrome Mox"):
                s.ok()                                                    # "you may exile a card": yes
                time.sleep(0.3)
            elif prompt.get("selecting") and "Select a card from your hand" in prompt["message"]:
                s.click_card(next(c for c in mine("hand") if c["name"] == "Counterspell")["id"])
                time.sleep(0.3)
            elif s.state["stack"] and prompt["ok"]["enabled"]:
                s.ok()                                                    # let the Mox (then its trigger) resolve
                time.sleep(0.3)
        moxes = [c for c in mine("battlefield") if c["name"] == "Chrome Mox"]
        self.assertTrue(moxes, "the Mox never came out")
        self.assertEqual(moxes[0].get("imprinted"), [{"name": "Counterspell", "colors": ["U"]}])
        self.assertEqual([c["name"] for c in mine("exile")], ["Counterspell"])

    def test_real_log_lines_read_cleanly(self):
        """Run some real turns and push Forge's own log text through the formatter: no card numbers, no brackets."""
        import forge_log as fl
        s = self.start(self.kinnan, self.kinnan, seed=3)
        bot = Bot(s)
        bot.run(150, until=lambda sess: (sess.state or {}).get("turn", 0) >= 5)
        f = fl.LogFormatter()
        f.context("Tester", [o["name"] for o in s.opponents()], {c["name"] for c in s._cards.values() if not c.get("hidden")})
        rows = [r for e in s.log for r in f.format(e)]
        text = "\n".join(r.text() for r in rows if r.kind == "line")
        self.assertTrue(text)
        for junk in ("[Zone Changer", "picked {", "Add {"):
            self.assertNotIn(junk, text)
        import re
        self.assertFalse(re.search(r"\s\(\d+\)", text), text[:500])
        self.assertTrue(any(r.kind == "turn" for r in rows))

    def play_opening(self, s, mulligans=0):
        """Draw first when asked, keep the hand (after `mulligans` mulligans), take the opening-hand action.
        True = offered, None = we are on the play."""
        offered = False
        t0 = time.time()
        taken, last, since = 0, None, time.time()
        while time.time() - t0 < 120 and not (s.exited or s.fatal):
            s.poll()
            while s.requests:
                req = s.requests[0]
                if "opening hand" in req.get("title", "").lower():
                    offered = True
                    s.answer(req, [0])
                else:
                    s.answer(req, True if req["kind"] == "confirm" else list(range(max(req.get("min", 1), 1))))
            st = s.state
            if st:
                p, msg = st["prompt"], st["prompt"]["message"]
                key = (msg, p["ok"]["enabled"], len(s.me()["zones"]["hand"]) if s.me() else 0)
                if key != last:                             # decide only when Forge has stopped changing the prompt
                    last, since = key, time.time()
                settled = time.time() - since > 0.6
                if "coin toss" in msg:
                    s.cancel()                              # "Draw"
                elif settled and s.is_mulligan_prompt() and p["ok"]["enabled"]:
                    since = time.time() + 5
                    if taken < mulligans:
                        taken += 1
                        s.cancel()
                    else:
                        s.ok()
                elif settled and msg.startswith("Return "):
                    since = time.time() + 5
                    for c in s.me()["zones"]["hand"][:int(msg.split()[1])]:
                        s.click_card(c["id"])
                        time.sleep(0.3)
                    s.ok()
                elif p["selecting"] and offered:
                    hand = [c for c in s.me()["zones"]["hand"] if c.get("selectable") or c.get("weak")]
                    if hand:
                        s.click_card(hand[0]["id"])
                        time.sleep(0.3)
                        s.ok()
                if st.get("turn", 0) >= 1:
                    return offered or (None if st.get("activePlayer") == st.get("me") else False)
                if offered and any(c["name"] == "Gemstone Caverns" for c in s.me()["zones"]["battlefield"]):
                    time.sleep(0.5)
                    s.poll()
                    return True
            time.sleep(0.1)
        return False


if __name__ == "__main__":
    unittest.main()
