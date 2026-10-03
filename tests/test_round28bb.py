# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28bb: what the first valid soak night found (27 Sept, run_20260927_094456: 174 games, 16 stalls, 1 crash).

  WinTests            winning a game froze it (Forge's achievement trophy needs its skin)          live, real bug
  AttachChoiceTests   "select a creature to attach to" had nothing marked selectable (Retether)   live, real bug
  WeakGlowRaceTests   a snapshot could be lost to a race on the "can play" list (night 1, game 99)  source check
  SoakLoopTests       the soak loop asked the bot while waiting, using up cards (10 of the 16 stalls)
  BotReplayTests      the bot's stalls, replayed from the night's own recordings (5 games)
  BotMulliganTests    the bot mulligans a hand with fewer than 2 or more than 5 lands
  SoakOptionsTests    "--hours 8" alone no longer means "1 game"
Plus tests/test_round28b.py: an AI's stranded commander is no longer reported (17 games of noise).
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live

FIX = os.path.join(BASE, "tests", "fixtures", "soak")
KINNAN = os.path.join(BASE, "sample_decks", "kinnan_nbc_moxfield_export.txt")


def load_states(name):
    with gzip.open(os.path.join(FIX, name), "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def replay(name, seed=1):
    """Feed a game's recorded states to a fresh bot in order, with one memory for the whole game, as tools/soak.py does
    (messages go into mem["infos"], tagged with the question they came during). Returns the bot's move for the LAST
    state - the one the game stalled on - and that state."""
    import soak_bot
    bot = soak_bot.SoakBot(seed)
    mem, move, last = {}, None, None
    moves_now = []                     # every move made during the last question (it may take a click, then an OK)
    for m in load_states(name):
        if m.get("t") in ("message", "info"):
            info = dict(m)
            info["_seq"] = (last or {}).get("inputSeq")
            mem.setdefault("infos", []).append(info)
        else:
            if last is None or m.get("inputSeq") != last.get("inputSeq"):
                moves_now = []
            last = m
        move = bot.next_action(last, [], mem)          # soak.py asks again after every message too
        moves_now.append(move)
    replay.moves_now = moves_now
    return move, last


def selectable_ids(state):
    return {c["id"] for p in state["players"] for cs in p["zones"].values() for c in cs if c.get("selectable")}


class BotReplayTests(unittest.TestCase):
    """The night's stalls, replayed state by state from the recordings. The first three pass on the old bot too: replayed
    one call per state, it does click. What made them stall live was the soak loop asking the bot again on every poll
    while a click was on its way (SoakLoopTests, red on the old loop). They stay as regression tests, together with the
    bot's click memory now lasting one question instead of the whole game (a card Sylvan Library put back and drew again
    keeps its id). The last three fail on the old bot: players as targets and Forge's "must block" message."""

    def assert_clicks_a_selectable_card(self, name):
        move, state = replay(name)
        self.assertIsNotNone(move, "the bot had nothing to do at: %r" % state["prompt"]["message"][:80])
        self.assertEqual(move[0], "click", move)
        self.assertIn(move[1], selectable_ids(state))

    def test_sylvan_library_choose_two_again(self):                  # game 1 (also 89, 99, 160)
        self.assert_clicks_a_selectable_card("valid1_game001_states.jsonl.gz")

    def test_butcher_of_malakir_second_sacrifice(self):              # game 75 (also 113; Ruthless Winnower 10, 31)
        self.assert_clicks_a_selectable_card("valid1_game075_states.jsonl.gz")

    def test_cleanup_discard_again(self):                            # game 105 (also 135)
        self.assert_clicks_a_selectable_card("valid1_game105_states.jsonl.gz")

    def test_blood_artist_targets_a_player(self):                    # game 132
        move, state = replay("valid1_game132_states.jsonl.gz")
        self.assertIsNotNone(move)
        self.assertEqual(move[0], "player", move)
        alive = {p["id"] for p in state["players"] if not p.get("lost")}
        self.assertIn(move[1], alive)

    def test_a_required_blocker_named_by_forge_is_clicked(self):     # game 69 (also 131): The Masamune, "must block"
        replay("valid1_game069_states.jsonl.gz")
        self.assertIn(("click", 62), replay.moves_now, "Kor Spiritdancer (62) is the creature Forge said must block")

    def test_an_old_must_block_message_is_ignored(self):
        """A message from an earlier question (a combat two turns ago) must not make the bot click anything now."""
        import soak_bot
        states = [m for m in load_states("valid1_game069_states.jsonl.gz") if m.get("t") == "state"]
        last = dict(states[-1])
        last["inputSeq"] = last["inputSeq"] + 7
        mem = {"infos": [{"t": "message", "text": "Kor Spiritdancer (62) must block an attacker, but has not been "
                          "assigned to block any.", "_seq": last["inputSeq"] - 7}]}
        self.assertIsNone(soak_bot.SoakBot(1)._required_block_move(last, last["players"][0], last["prompt"], mem))


class BotMulliganTests(unittest.TestCase):
    def state(self, hand, taken=0):
        cards = [{"id": i, "name": n, "isLand": n in ("Forest", "Island")} for i, n in enumerate(hand)]
        return {"me": 0, "inputSeq": 1, "players": [{"id": 0, "name": "Soak", "zones": {"hand": cards}}],
                "prompt": {"message": "Soak, you are going first.\n\nDo you want to keep your hand?",
                           "ok": {"label": "Keep", "enabled": True}, "cancel": {"label": "Mulligan", "enabled": True}}}

    def test_no_land_hand_is_mulliganed_then_kept_after_two(self):
        import soak_bot
        bot, mem = soak_bot.SoakBot(1), {}
        st = self.state(["Brainstorm"] * 7)
        self.assertEqual(bot.next_action(st, [], mem), ("cancel",))
        st["inputSeq"] = 2
        self.assertEqual(bot.next_action(st, [], mem), ("cancel",))
        st["inputSeq"] = 3
        self.assertEqual(bot.next_action(st, [], mem), ("ok",), "at most two mulligans, then keep")

    def test_three_lands_is_kept(self):
        import soak_bot
        st = self.state(["Forest", "Island", "Forest", "Brainstorm", "Ponder", "Sol Ring", "Opt"])
        self.assertEqual(soak_bot.SoakBot(1).next_action(st, [], {}), ("ok",))

    def test_seven_lands_is_mulliganed(self):
        import soak_bot
        st = self.state(["Forest"] * 7)
        self.assertEqual(soak_bot.SoakBot(1).next_action(st, [], {}), ("cancel",))


class SoakOptionsTests(unittest.TestCase):
    def parsed(self, argv):
        import soak
        seen = {}
        orig = soak.run
        soak.run = lambda args: seen.setdefault("args", args)
        try:
            soak.main(argv)
        finally:
            soak.run = orig
        return seen["args"]

    def test_hours_alone_has_no_game_limit(self):
        self.assertIsNone(self.parsed(["--hours", "8"]).games)

    def test_nothing_given_is_one_game(self):
        self.assertEqual(self.parsed([]).games, 1)

    def test_both_given_keeps_both(self):
        a = self.parsed(["--hours", "8", "--games", "30"])
        self.assertEqual((a.games, a.hours), (30, 8.0))


class SoakFolderTests(unittest.TestCase):
    def test_a_portable_copy_keeps_soak_runs_next_to_the_program(self):
        """With Round 28 in, 28b's default would have been <program folder>/soak - not in .gitignore or backup.py's skip
        list, so the backup would have pushed the recordings to GitHub."""
        import soak
        old = os.environ.get("MANTICORE_PORTABLE")
        os.environ["MANTICORE_PORTABLE"] = "1"
        try:
            self.assertEqual(soak.default_out_dir(), os.path.join(soak.BASE_DIR, "soak_runs"))
        finally:
            if old is None:
                os.environ.pop("MANTICORE_PORTABLE", None)
            else:
                os.environ["MANTICORE_PORTABLE"] = old

    def test_an_installed_copy_uses_its_local_data_folder(self):
        import paths
        import soak
        old = os.environ.get("MANTICORE_PORTABLE")
        os.environ["MANTICORE_PORTABLE"] = "0"
        try:
            self.assertEqual(soak.default_out_dir(), os.path.join(paths.local_dir(), "soak"))
            self.assertFalse(soak.default_out_dir().startswith(os.path.join(soak.BASE_DIR, "")))
        finally:
            if old is None:
                os.environ.pop("MANTICORE_PORTABLE", None)
            else:
                os.environ["MANTICORE_PORTABLE"] = old

    def test_soak_runs_is_what_git_and_the_backup_skip(self):
        with open(os.path.join(BASE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("soak_runs/", f.read())
        import backup
        self.assertIn("soak_runs", backup.TOP_LEVEL_SKIP)


class WeakGlowRaceTests(unittest.TestCase):
    def test_snapshot_survives_a_failed_read_of_the_can_play_list(self):
        """Night 1 (old code), game 99: ArrayIndexOutOfBoundsException in getWeakSelectableStrength while the game thread
        changed the list; that whole snapshot was lost. Java can't be unit-tested here, so check the guard is in place."""
        with open(os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Snapshot.java"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("gui.getWeakSelectableStrength(c)")
        self.assertIn("try {", src[i - 200:i])
        self.assertIn("catch (RuntimeException", src[i:i + 200])


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class WinTests(unittest.TestCase):
    def test_winning_reaches_game_over(self):
        """Before 28bb: the AI at 0 life -> "Checker has won" in the log, then Forge's achievement update threw
        "Can't find an image for FSkinProp IMG_COMMON_TROPHY" and game_over never came (reproduced live)."""
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=0"])
        # Wait for the board change before touching anything: Forge applies a set-up on another thread, and an OK
        # passed meanwhile restarts the game loop beside it (ConcurrentModificationException, the game thread dies;
        # seen 2 runs in 6 before this wait). Only the dev set-up does this; a real game has no second thread.
        end = time.time() + 20
        while time.time() < end and not s.game_over and not any(
                p.get("lost") or p.get("life", 1) <= 0 for p in (s.state or {}).get("players", []) if p["id"] != s.state.get("me")):
            s.poll()
            time.sleep(0.2)
        end = time.time() + 45
        while time.time() < end and not s.game_over:
            s.poll()
            if (s.state or {}).get("prompt", {}).get("ok", {}).get("enabled") and not s.game_over:
                s.ok()
            time.sleep(0.3)
        self.assertTrue(s.game_over, "no game_over after winning")
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("IMG_COMMON_TROPHY", f.read())


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class AttachChoiceTests(unittest.TestCase):
    def test_retether_marks_the_creatures_it_can_attach_to(self):
        """Before 28bb nothing was marked (Forge offers views of copies of the cards, compared by identity), so the table
        showed no glow and the bot had nothing to click (night 1, games 53 and 149). Clicking a creature always worked."""
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40",
                 "humanbattlefield=Plains;Plains;Plains;Plains;Grizzly Bears;Llanowar Elves",
                 "humangraveyard=Pariah", "humanhand=Retether", "aibattlefield=Hill Giant"])

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.1)

        pump(3)
        retether = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Retether")
        s.click_card(retether["id"])
        end = time.time() + 60
        while time.time() < end and s.state.get("input") != "InputSelectEntitiesFromList":
            pump(1)
            msg = (s.state.get("prompt") or {}).get("message") or ""
            if "Pay Mana" in msg or (msg.startswith("Priority") and s.state.get("stack")):
                s.ok()
        self.assertEqual(s.state.get("input"), "InputSelectEntitiesFromList")
        self.assertIs(s.state.get("asking"), True, "round 28bb: snapshots say whether Forge is asking something")
        marked = {c["name"] for p in s.state["players"] for c in p["zones"]["battlefield"] if c.get("selectable")}
        self.assertEqual(marked, {"Grizzly Bears", "Llanowar Elves", "Hill Giant"})
        bears = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Grizzly Bears")
        s.click_card(bears["id"])
        pump(3)
        pariah = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Pariah")
        self.assertEqual(pariah.get("attachedTo"), bears["id"])


def _write_slow_pick_two_bridge():
    """A stand-in bridge with Sylvan Library's question: "choose 2" of 3 cards, OK greyed. Each card click takes Forge a
    second to answer (a new state with the SAME question number, as the real bridge does inside one question); after two
    different cards it ends the game. Written under the system temp folder."""
    fd, path = tempfile.mkstemp(suffix="_bridge_pick_two.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys, time\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "picked = []\n"
            "def state():\n"
            "    hand = [{'id': i, 'name': n, 'selectable': True, 'highlight': i in picked} for i, n in ((11, 'Opt'), (12, 'Ponder'), (13, 'Brainstorm'))]\n"
            "    out(t='state', turn=3, phase='DRAW', me=0, activePlayer=0, input='InputSelectCardsFromList', inputSeq=87,\n"
            "        players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': hand, 'battlefield': []}},\n"
            "                 {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "        prompt={'message': 'Sylvan Library (43)\\n - \\nChoose a card ', 'selecting': True, 'selMin': 2, 'selMax': 2,\n"
            "                'ok': {'label': 'OK', 'enabled': False}, 'cancel': {'label': 'Cancel', 'enabled': False}})\n"
            "out(t='ready', protocol=2)\n"
            "state()\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    if cmd['c'] == 'card':\n"
            "        time.sleep(1.0)\n"
            "        if cmd['id'] not in picked: picked.append(cmd['id'])\n"
            "        if len(picked) >= 2:\n"
            "            out(t='game_over'); continue\n"
            "        state()\n"
        )
    return path


def _write_first_ok_dropped_bridge():
    """"Return 0 card(s) to the bottom of your library" with OK enabled; the first OK is dropped (as the real bridge drops a
    click that arrives between questions), the second ends the game."""
    fd, path = tempfile.mkstemp(suffix="_bridge_ok_dropped.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready', protocol=2)\n"
            "out(t='state', turn=0, phase='', me=0, input='InputLondonMulligan', inputSeq=5,\n"
            "    players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "             {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Return 0 card(s) to the bottom of your library', 'selecting': False,\n"
            "            'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'Auto', 'enabled': False}})\n"
            "oks = 0\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    if cmd['c'] == 'ok':\n"
            "        oks += 1\n"
            "        if oks == 1:\n"
            "            out(t='dropped', c='ok', at=cmd.get('at'), now=5)\n"
            "        else:\n"
            "            out(t='game_over')\n"
        )
    return path


def _write_between_questions_bridge():
    """After the OK, Forge first sends a snapshot with NO question (input "") that still shows the old prompt and an
    enabled OK, then the next real question 1 s later; the game ends on the second OK."""
    fd, path = tempfile.mkstemp(suffix="_bridge_between.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys, time\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "def state(seq, inp):\n"
            "    out(t='state', turn=5, phase='MAIN1', me=0, activePlayer=0, input=inp, inputSeq=seq, asking=bool(inp),\n"
            "        players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "                 {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "        prompt={'message': 'Priority: Soak\\nTurn: 5 (Soak)\\nPhase: Main phase, precombat\\nStack: Empty',\n"
            "                'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End Turn', 'enabled': True}})\n"
            "out(t='ready', protocol=2)\n"
            "state(11, 'InputPassPriority')\n"
            "oks = 0\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    if cmd['c'] == 'ok':\n"
            "        if cmd.get('at') == 12:\n"
            "            out(t='dropped', c='ok', at=12, now=12); continue\n"
            "        oks += 1\n"
            "        if oks == 1:\n"
            "            state(12, ''); time.sleep(1.0); state(13, 'InputPassPriority')\n"
            "        else:\n"
            "            out(t='game_over')\n"
        )
    return path


def _write_cleanup_discard_bridge():
    """The cleanup discard: a real question whose Input class is anonymous, so "input" is "" - but "asking" is true."""
    fd, path = tempfile.mkstemp(suffix="_bridge_discard.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "hand = [{'id': i, 'name': 'Island', 'selectable': True} for i in range(21, 29)]\n"
            "out(t='ready', protocol=2)\n"
            "out(t='state', turn=2, phase='CLEANUP', me=0, activePlayer=0, input='', asking=True, inputSeq=25,\n"
            "    players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': hand, 'battlefield': []}},\n"
            "             {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Cleanup Phase\\nSelect 1 card(s) to discard to bring your hand down to the maximum of 7 cards.',\n"
            "            'selecting': True, 'selMin': 1, 'selMax': 1,\n"
            "            'ok': {'label': 'OK', 'enabled': False}, 'cancel': {'label': 'Cancel', 'enabled': False}})\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    if cmd['c'] == 'card':\n"
            "        out(t='game_over')\n"
        )
    return path


def _write_chatty_bridge():
    """One question ("Priority", OK enabled, question 7) and, while Forge "thinks" about the OK, ten more snapshots of the
    same question (as while the AIs act); the game ends 1.5 s after the first OK."""
    fd, path = tempfile.mkstemp(suffix="_bridge_chatty.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys, time\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "def state(life):\n"
            "    out(t='state', turn=5, phase='END_OF_TURN', me=0, activePlayer=1, input='InputPassPriority', inputSeq=7,\n"
            "        players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "                 {'id': 1, 'name': 'AI', 'life': life, 'zones': {'battlefield': []}}],\n"
            "        prompt={'message': 'Priority: Soak\\nTurn: 5 (AI)\\nPhase: End step\\nStack: Empty',\n"
            "                'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End Turn', 'enabled': True}})\n"
            "out(t='ready', protocol=2)\n"
            "state(40)\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    if cmd['c'] == 'ok':\n"
            "        for i in range(10):\n"
            "            time.sleep(0.1); state(39 - i)\n"
            "        time.sleep(0.5); out(t='game_over')\n"
        )
    return path


class SoakLoopTests(unittest.TestCase):
    def test_a_pick_two_question_gets_two_different_cards(self):
        """Before 28bb the soak loop asked the bot on every poll while it waited for Forge to answer the first click, and
        each ask "used" another card; with the answer taking a second, the bot had no card left for the second pick and
        the game stalled (night 1: Sylvan Library, games 1, 89, 99, 160)."""
        import argparse
        import soak
        bridge = _write_slow_pick_two_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=20.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                summary = f.read()
        self.assertIn("ended=game_over", summary, summary[-800:])

    def test_an_ok_that_was_dropped_is_sent_again(self):
        """28bb's first sandbox soak (and night 0's game 140): the first OK was dropped; asked every 20 ms during the
        resend cooldown, the bot counted the prompt as seen past its limit of 3 and never sent OK again - a 4-player game
        stalled before turn 1 on "Return 0 card(s) to the bottom of your library"."""
        import argparse
        import soak
        bridge = _write_first_ok_dropped_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=12.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                summary = f.read()
        self.assertIn("ended=game_over", summary, summary[-800:])


    def test_more_snapshots_of_the_same_question_get_one_answer(self):
        """28bb's first sandbox soak: asked once per snapshot, the bot sent the same OK again for every snapshot the AIs'
        actions produced - 170 to 430 dropped clicks a game. Once per question as it stands."""
        import argparse
        import soak
        bridge = _write_chatty_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=12.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with gzip.open(os.path.join(out, "game_000", "record.jsonl.gz"), "rt", encoding="utf-8") as f:   # round 28d fix 1: kept gzipped
                sent = [json.loads(line) for line in f if '"_sent"' in line]
        oks = [m for m in sent if (m.get("cmd") or {}).get("c") == "ok"]
        self.assertEqual(len(oks), 1, oks)

    def test_nothing_is_sent_while_forge_asks_nothing(self):
        """28bb's second sandbox soak: after each OK Forge briefly asks nothing (input "") while the snapshot still shows
        the old prompt with OK enabled; the bot answered it and the bridge dropped it - about 100 a game."""
        import argparse
        import soak
        bridge = _write_between_questions_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=12.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with gzip.open(os.path.join(out, "game_000", "record.jsonl.gz"), "rt", encoding="utf-8") as f:   # round 28d fix 1: kept gzipped
                lines = [json.loads(line) for line in f]
        self.assertTrue(any(m.get("t") == "game_over" for m in lines))
        self.assertEqual([m for m in lines if m.get("t") == "dropped"], [])

    def test_a_question_with_an_anonymous_input_class_is_still_answered(self):
        """28bb's third sandbox soak stalled at the cleanup discard: its Input class is anonymous, so "input" is "", which
        the loop had taken to mean "Forge asks nothing". The bridge's "asking" flag says what's true."""
        import argparse
        import soak
        bridge = _write_cleanup_discard_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=8.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                summary = f.read()
        self.assertIn("ended=game_over", summary, summary[-800:])

if __name__ == "__main__":
    unittest.main()
