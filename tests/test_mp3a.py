# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP3a (Karl, 27 Oct 2026: "Add brawl, standard, and draft to online play"): online games in every format.

The host's deck's format is the game's format (Commander, Brawl, Standard, Commander Draft, Draft); NetHost registers every seat
and starts the match as Main does for that format (Main.register / Main.variant), applies the deck's house rules (Turbo Cube's
emblem) to every seat, and refuses a guest's deck of another format in plain words. The invite code and the welcome carry the
format and the house rules; the protocol is 3.

Fast tests need no Java (formats, invite codes, the host's command line, the guest's session, the table's dialogs, the online
soak's format checks, the Java source). Live tests run the real NetHost with a NetSession on 127.0.0.1: a 1v1 game per format
(starting life, the start check, the house rule, a concede to game over on both tables), the mulligan rule, a pod with an AI seat
per format, a mismatched deck refused, Standard and Draft decks at one table, hidden information in a Standard game, and a Brawl
game continued from its save.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_net as fn
import formats
import house_rules
import online_screens as onl
import tests.live as live
import tests.test_deck_screen as tds
from tests.test_mp1 import FakeHostSession, free_port, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "sample_decks")
JAVA = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge")


def _src(name):
    with open(os.path.join(JAVA, name), encoding="utf-8") as f:
        return f.read()


def make_deck(folder, sample, fmt, name, rules=(), limit=None):
    """A .dck of a sample deck written as `fmt` (a Draft deck: the first `limit` cards of a 60-card list)."""
    from deck_loader import load_deck
    c, d = load_deck(os.path.join(SAMPLES, sample))
    if limit:
        d = list(d)[:limit]
    return fc.write_deck_file(os.path.join(folder, name + ".dck"), c, d, name, fmt=fmt, rules=rules)


# ---------------------------------------------------------------------------------------------------------------------
# 1. formats, invite codes, the Java source
# ---------------------------------------------------------------------------------------------------------------------
class FormatTests(unittest.TestCase):
    def test_every_format_is_online(self):
        for key in formats.FORMATS:
            self.assertTrue(formats.get(key).online, key)

    def test_which_decks_sit_together(self):
        self.assertTrue(formats.online_match("commander", "commander_draft"))
        self.assertTrue(formats.online_match("commander_draft", "commander"))
        self.assertTrue(formats.online_match("standard", "draft"))
        self.assertTrue(formats.online_match("brawl", "brawl"))
        for a, b in (("brawl", "commander"), ("commander", "brawl"), ("standard", "commander"), ("draft", "brawl"),
                     ("commander_draft", "standard")):
            self.assertFalse(formats.online_match(a, b), (a, b))

    def test_the_mismatch_words(self):
        self.assertEqual(formats.online_mismatch("brawl", "commander"),
                         "This is a Brawl game: choose a Brawl deck (yours is a Commander deck).")
        self.assertEqual(formats.online_mismatch("draft", "brawl"),
                         "This is a Draft game: choose a Standard or Draft deck (yours is a Brawl deck).")
        self.assertEqual(formats.online_mismatch("commander", "standard"),
                         "This is a Commander game: choose a Commander or Commander Draft deck (yours is a Standard deck).")

    def test_the_java_side_says_the_same(self):
        """OnlineFormats.java and formats.py: the same names, deck-screen groups, engines and words."""
        src = _src("OnlineFormats.java")
        names = dict(re.findall(r'NAMES\.put\("(\w+)", "([^"]+)"\)', src))
        self.assertEqual(names, {k: f.name for k, f in formats.FORMATS.items()})
        for k, v in formats.SCREEN.items():
            self.assertIn('if ("%s".equals(key)) return "%s";' % (k, v), src)
        self.assertIn('"This is a " + name(game) + " game: choose " + want + " (yours is a " + name(deck) + " deck)."', src)
        self.assertIn('case "commander": want = "a Commander or Commander Draft deck"', src)
        self.assertIn('case "standard": want = "a Standard or Draft deck"', src)
        self.assertIn('case "constructed": return "standard";', src)
        self.assertIn('case "limited": return "draft";', src)

    def test_the_protocol_is_3_on_both_sides(self):
        self.assertEqual(fn.PROTOCOL_V, 3)
        self.assertIn("static final int PROTOCOL_V = 3;", _src("NetHost.java"))

    def test_nethost_registers_seats_as_main_does(self):
        src = _src("NetHost.java")
        self.assertNotIn("RegisteredPlayer.forCommander(", src)
        self.assertEqual(src.count("Main.register("), 3)                  # host, each guest, each AI seat
        self.assertIn("GameType variant = Main.variant();", src)
        self.assertNotIn("startMatch(GameType.Commander", src)
        self.assertIn("OnlineFormats.compatible(gameFormat, fmt)", src)
        self.assertIn("HouseRules.active.add(r)", src)

    def test_invite_codes_carry_the_format(self):
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw", fmt="draft", rules=("turbo",))
        inv = fn.read_invite(code)
        self.assertEqual((inv["format"], inv["rules"]), ("draft", ("turbo",)))
        old = fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw")
        self.assertEqual(old, fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw", fmt="commander"))  # Commander: as before
        self.assertEqual((fn.read_invite(old)["format"], fn.read_invite(old)["rules"]), ("commander", ()))

    def test_the_hello_names_the_decks_format(self):
        line = json.loads(fn.hello_line("Sam", "pw", "c", "D", "[metadata]\nDeck Type=Brawl\n", "brawl"))
        self.assertEqual((line["v"], line["deck"]["format"]), (3, "brawl"))

    def test_a_version_refusal_says_update_both(self):
        self.assertIn("Update both", fn.join_message("version", "host speaks v3"))


# ---------------------------------------------------------------------------------------------------------------------
# 2. the sessions
# ---------------------------------------------------------------------------------------------------------------------
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mp3a_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)

    def args(self, fmt, rules=()):
        dck = make_deck(self.tmp, "brawl_nissa.txt" if fmt == "brawl" else "standard_red_aggro.txt", fmt, "me", rules=rules,
                        limit=40 if fmt == "draft" else None)
        return fc.HostSession(dck, "Karl", 36800, "pw", self.cert, "guest.dck", upnp=False)._bridge_args()

    def test_the_host_command_line_per_format(self):
        a = self.args("brawl")
        self.assertEqual(a[a.index("--format") + 1:a.index("--format") + 4], ["brawl", "--game-format", "brawl"])
        a = self.args("draft", ("turbo",))
        self.assertEqual(a[a.index("--format") + 1], "constructed")
        self.assertEqual(a[a.index("--game-format") + 1], "draft")
        self.assertEqual(a[a.index("--house-rule") + 1], "turbo")
        dck = make_deck(self.tmp, "typal_lathril.txt", "commander", "cmd")
        a = fc.HostSession(dck, "Karl", 36800, "pw", self.cert, "guest.dck", upnp=False)._bridge_args()
        self.assertNotIn("--format", a)                                   # a Commander game's command line is unchanged
        self.assertNotIn("--house-rule", a)

    def test_the_guest_takes_the_format_from_the_welcome(self):
        from tests.net_fake import SeatHost, plain_connect
        host = SeatHost(welcome_extra={"gameFormat": "draft", "houseRules": ["turbo"]}).start()
        self.addCleanup(host.stop)
        g = fc.NetSession("127.0.0.1", host.port, "Sam", "pw", "D", "[metadata]\nDeck Type=Limited\n")
        g._connect = plain_connect
        g.start()
        self.addCleanup(g.close)
        self.assertEqual((g.fmt, g.rules), ("draft", ("turbo",)))
        g.handle({"t": "ready", "protocol": 2, "format": "constructed", "houseRules": ["turbo"]})
        self.assertIsNone(g.fatal)
        g2 = fc.NetSession("127.0.0.1", host.port, "Ann", "pw", "D", "x")
        g2.fmt, g2.rules = "brawl", ()                                  # a bridge that started the wrong game is fatal
        g2.handle({"t": "ready", "protocol": 2, "format": "commander"})
        self.assertIn("Commander game instead of Brawl", g2.fatal)


# ---------------------------------------------------------------------------------------------------------------------
# 3. the table
# ---------------------------------------------------------------------------------------------------------------------
class TableTests(tds.TempDecks):
    def setUp(self):
        super().setUp()
        for stem in ("brawl_nissa", "brawl_krenko", "standard_red_aggro"):
            shutil.copy(os.path.join(SAMPLES, stem + ".txt"), self.samples)
        self.cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)

    def host(self, gui, **kw):
        made = {}

        def fake_host(*a, **k):
            made["args"], made["kw"] = a, k
            return FakeHostSession(*a, **k)
        with mock.patch.object(fn, "ensure_host_cert", return_value=self.cert), \
                mock.patch.object(fc, "HostSession", side_effect=fake_host):
            err = gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", False, **kw)
        return err, made

    def test_hosting_a_brawl_game_writes_a_brawl_deck(self):
        gui = self.idle_gui()
        gui.menu.set_format("brawl")
        err, made = self.host(gui)
        self.assertIsNone(err)
        with open(made["args"][0], encoding="utf-8") as f:
            self.assertIn("Deck Type=Brawl", f.read())
        self.assertEqual(gui.session.fmt, "brawl")
        self.assertEqual(gui.session.stats_info["format"], "brawl")
        code = None
        gui.session.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        gui.hosting_wait.address.set("203.0.113.7")
        code = gui.hosting_wait.invite()
        self.assertEqual(fn.read_invite(code)["format"], "brawl")

    def test_ai_seats_must_be_of_the_games_format(self):
        gui = self.idle_gui()
        gui.menu.set_format("brawl")
        from deck_library import list_decks
        kinnan = next(e for e in list_decks(*self.dirs) if e.format == "commander")
        gui.menu.resolve_seats = lambda: ([kinnan], None)
        err, _made = self.host(gui, guests=1, ai=1)
        self.assertEqual(err, "AI 1's deck is a Commander deck, and this is a Brawl game.")

    def test_the_host_dialog_names_the_format(self):
        gui = self.idle_gui()
        gui.menu.set_format("standard")
        gui.menu.press(gui, "host_online")
        texts = []
        real = onl.wrap_text
        with mock.patch.object(onl, "wrap_text", side_effect=lambda t, *a, **k: texts.append(t) or real(t, *a, **k)):
            gui.modal.draw(gui)
        self.assertTrue(any("This is a Standard game: your friends need a Standard or Draft deck." in t for t in texts), texts)

    def test_join_with_the_wrong_format_says_so_and_switches_the_deck_screen(self):
        gui = self.idle_gui()                                            # the screen shows Commander; my deck is Commander
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.fields["code"].set(fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw", fmt="brawl"))
        d.fields["name"].set("Sam")
        with mock.patch.object(gui, "make_guest_session") as mk:
            d.join(gui)
            mk.assert_not_called()                                       # nothing sent: told before connecting
        self.assertIn("This is a Brawl game: choose a Brawl deck (yours is a Commander deck).", d.message[0])
        self.assertIn("press Join again", d.message[0])
        self.assertEqual(gui.menu.fmt, "brawl")
        self.assertEqual(d.entry.format, "brawl")
        with mock.patch.object(gui, "make_guest_session", return_value=(None, "stop here")) as mk:
            d.join(gui)                                                  # the second time it goes ahead with the Brawl deck
            mk.assert_called_once()
            self.assertEqual(mk.call_args[0][0].format, "brawl")

    def test_join_with_no_deck_of_that_format(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.fields["code"].set(fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw", fmt="draft"))
        d.fields["name"].set("Sam")
        with mock.patch.object(gui, "make_guest_session") as mk:
            d.join(gui)
            mk.assert_not_called()
        self.assertIn("This is a Draft game", d.message[0])
        if not [e for e in gui.menu.in_format() if formats.online_match("draft", e.format)]:
            self.assertIn("no Standard decks yet", d.message[0])

    def test_the_guest_writes_its_deck_in_its_format(self):
        gui = self.idle_gui()
        gui.menu.set_format("brawl")
        s, err = gui.make_guest_session(gui.menu.mine, "Sam", "127.0.0.1", 36800, "pw", "ab" * 32)
        self.assertIsNone(err)
        self.assertIn("Deck Type=Brawl", s.dck_text)

    def test_the_loading_line_counts_the_online_seats(self):
        gui = self.idle_gui()
        s = mock.Mock(fmt="brawl", rules=(), online="host", guests_wanted=2, opponent_paths=[])
        gui.session = s
        self.assertEqual(gui.format_line(), "Brawl - 30 life each, no commander damage.")
        gui.session = mock.Mock(fmt="brawl", rules=(), online="guest", players=2, opponent_paths=[])
        self.assertEqual(gui.format_line(), "Brawl - 25 life each, no commander damage.")
        gui.session = mock.Mock(fmt="draft", rules=("turbo",), online="guest", players=3, opponent_paths=[])
        self.assertTrue(gui.format_line().startswith("Draft - 20 life each. Turbo Cube emblem"))

    def test_the_save_keeps_the_format(self):
        import online_save
        meta = {"format": "brawl", "turn": 12, "guests": [{"name": "Sam"}], "saved": 0}
        self.assertTrue(online_save.describe(meta).startswith("Brawl, turn 12, with Sam"))
        self.assertTrue(online_save.describe(dict(meta, format="commander")).startswith("turn 12, with Sam"))


# ---------------------------------------------------------------------------------------------------------------------
# 4. the online soak's format checks
# ---------------------------------------------------------------------------------------------------------------------
class SoakTests(unittest.TestCase):
    def seat(self, label, lives, command=None, dmg=0):
        from tools import online_soak as osk
        st = osk.Seat.__new__(osk.Seat)
        st.label, st.first_life, st.cmd_damage = label, lives, dmg
        st.first_command = command or {n: [] for n in (lives or {})}
        return st

    def test_life_damage_and_emblem_rules(self):
        from tools import online_soak as osk
        ok = self.seat("host", {"A": 25, "B": 25})
        self.assertEqual(osk.format_findings("brawl", (), 2, [ok]), [])
        bad = self.seat("host", {"A": 40, "B": 25})
        self.assertEqual([f["rule"] for f in osk.format_findings("brawl", (), 2, [bad])], ["online_life"])
        pod = self.seat("guest", {"A": 30, "B": 30, "C": 30})
        self.assertEqual(osk.format_findings("brawl", (), 3, [pod]), [])
        hit = self.seat("host", {"A": 20, "B": 20}, dmg=2)
        self.assertEqual([f["rule"] for f in osk.format_findings("standard", (), 2, [hit])], ["online_cmd_damage"])
        self.assertEqual(osk.format_findings("commander", (), 2, [self.seat("h", {"A": 40, "B": 40}, dmg=5)]), [])
        em = self.seat("host", {"A": 20, "B": 20}, command={"A": ["Turbo Cube Emblem"], "B": []})
        self.assertEqual([f["rule"] for f in osk.format_findings("draft", ("turbo",), 2, [em])], ["online_house_rule"])

    def test_a_huge_record_is_read_cut_down(self):
        """The Brawl online soak's game 26 (a 518-token board) wrote 3.8 GB records in 20 minutes; _finish read records whole."""
        from tools import online_soak as osk
        osk._load_modules()
        tmp = tempfile.mkdtemp(prefix="mp3a_huge_")
        self.addCleanup(shutil.rmtree, tmp, True)
        rec = os.path.join(tmp, "host.jsonl")
        with open(rec, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": "ready", "protocol": 2}) + "\n" + json.dumps({"t": "game_over"}) + "\n")
        r = osk.OnlineResult(0, 1, ["a.txt", "b.txt"])
        r.ended = "game_over"
        with mock.patch.object(osk.soak, "RECORD_JUDGE_LIMIT", 10), mock.patch.object(osk.bridge_rules, "load_stream",
                                                                                  side_effect=AssertionError("read whole")):
            osk._finish(r, None, [], rec, [], os.path.join(tmp, "none.log"), tmp, time.time())
        self.assertIn("huge_record", r.warn_rules())
        self.assertEqual(r.fail_rules(), [])

    def test_note_format_view(self):
        from tools import online_soak as osk
        st = self.seat("host", None)
        st.first_life = None
        osk.note_format_view(st, {"players": [{"name": "A", "life": 20, "zones": {"command": [{"name": "Turbo Cube Emblem"}]},
                                               "commanderDamage": {}},
                                              {"name": "B", "life": 20, "zones": {}, "commanderDamage": {"7": 3}}]})
        self.assertEqual(st.first_life, {"A": 20, "B": 20})
        self.assertEqual(st.first_command, {"A": ["Turbo Cube Emblem"], "B": []})
        self.assertEqual(st.cmd_damage, 1)


# ---------------------------------------------------------------------------------------------------------------------
# 5. live: the real NetHost, a NetSession on 127.0.0.1
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp3a_live_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        t = cls.tmp
        cls.decks = {
            "commander": (make_deck(t, "typal_lathril.txt", "commander", "Lathril"),
                          make_deck(t, "spellslinger_veyran.txt", "commander", "Veyran")),
            "brawl": (make_deck(t, "brawl_nissa.txt", "brawl", "Nissa"), make_deck(t, "brawl_krenko.txt", "brawl", "Krenko"),
                      make_deck(t, "brawl_elas.txt", "brawl", "Elas")),
            "standard": (make_deck(t, "standard_red_aggro.txt", "standard", "Red"),
                         make_deck(t, "standard_green_dinos.txt", "standard", "Dinos"),
                         make_deck(t, "standard_dimir_surveil.txt", "standard", "Dimir")),
            "draft": (make_deck(t, "standard_red_aggro.txt", "draft", "DraftRed", rules=("turbo",), limit=40),
                      make_deck(t, "standard_green_dinos.txt", "draft", "DraftGreen", rules=("turbo",), limit=40),
                      make_deck(t, "standard_dimir_surveil.txt", "draft", "DraftBlue", rules=("turbo",), limit=40)),
            "commander_draft": (make_deck(t, "typal_lathril.txt", "commander_draft", "CDLathril"),
                                make_deck(t, "spellslinger_veyran.txt", "commander_draft", "CDVeyran"),
                                make_deck(t, "aristocrats_teysa.txt", "commander_draft", "CDTeysa")),
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start_host(self, deck, **kw):
        port = free_port()
        h = fc.HostSession(deck, "Karl", port, "pw-mp3a", self.cert, os.path.join(self.tmp, "guest_%d.dck" % port), upnp=False,
                           bind="127.0.0.1", code="hostcode", seed=kw.pop("seed", 7), **kw)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited or h.host_failed, 120, pump=[h]),
                        "NetHost never got ready")
        self.assertIsNone(h.host_failed)
        return h, port

    def guest(self, port, deck, name="Sam"):
        with open(deck, encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, name, "pw-mp3a", os.path.basename(deck), f.read(),
                              fingerprint=self.cert.fingerprint, code="guestcode")
        g.start()
        self.addCleanup(g.close)
        return g

    @staticmethod
    def msg(s):
        return ((s.state or {}).get("prompt") or {}).get("message", "")

    def to_priority(self, sessions, seconds=150):
        from tests.test_mp2c import LiveTests as MP2c
        # past the opening hands: somebody is asked for priority, or the first turn has a phase (patch 43's smart passing can
        # carry a quiet first turn past every priority stop)
        return MP2c.play_pregame(self, sessions, lambda: any(self.msg(s).startswith("Priority") or (
            ((s.state or {}).get("turn") or 0) >= 1 and (s.state or {}).get("phase")) for s in sessions), seconds)

    def started(self, fmt, guests=1, ai=0, seed=7, pregame=True):
        decks = self.decks[fmt]
        h, port = self.start_host(decks[0], seed=seed, **({"guests": guests, "ai_paths": list(decks[1 + guests:1 + guests + ai])}
                                                          if (guests, ai) != (1, 0) else {}))
        gs = [self.guest(port, decks[1 + k], "Sam" if k == 0 else "Ann") for k in range(guests)]
        everyone = [h] + gs
        self.assertTrue(wait_for(lambda: all(s.ready and s.state and s.me() and self.msg(s) for s in everyone), 90,
                                 pump=everyone), "the game didn't start for everyone")
        if pregame:
            # Forge fires GameEventGameStarted (Turbo Cube's emblem, HouseRules) only once the play-or-draw question is
            # answered, so the opening hands are played first
            self.assertTrue(self.to_priority(everyone), "the game never got past the opening hands")
            wait_for(lambda: all(self.zones_ready(fmt, s) for s in everyone), 20, pump=everyone)
        return h, gs

    @staticmethod
    def zones_ready(fmt, s):
        for p in s.state["players"]:
            cmd = [c.get("name") for c in (p.get("zones") or {}).get("command") or []]
            if formats.has_commander(fmt) and not p.get("commanders"):
                return False
            if fmt == "draft" and "Turbo Cube Emblem" not in cmd:
                return False
        return True

    def check_start(self, fmt, h, gs, players):
        life = formats.starting_life(fmt, players)
        for s in [h] + gs:
            self.assertIsNone(s.fatal, s.fatal)
            self.assertEqual(s.fmt, fmt)
            self.assertEqual(s.format_used, formats.engine(fmt))
            self.assertEqual(len(s.state["players"]), players)
            self.assertEqual([p["life"] for p in s.state["players"]], [life] * players, (fmt, s))
            for p in s.state["players"]:
                cmd = [c.get("name") for c in (p.get("zones") or {}).get("command") or []]
                if fmt == "draft":
                    self.assertIn("Turbo Cube Emblem", cmd, (s, p["name"]))
                if not formats.has_commander(fmt):
                    self.assertEqual([c for c in cmd if c != "Turbo Cube Emblem"], [], (s, p["name"]))
                    self.assertFalse(p.get("commanders"), p.get("commanders"))
                else:
                    self.assertTrue(p.get("commanders"), (fmt, p["name"]))
        wait_for(lambda: False, 1.0, pump=[h] + gs)
        self.assertEqual(h.checks, [], h.checks)                           # the start check: the right variant, or none

    def concede_to_game_over(self, h, gs):
        gs[0].concede()
        everyone = [h] + gs
        self.assertTrue(wait_for(lambda: all(s.game_over for s in everyone), 60, pump=everyone),
                        "game over didn't reach every table")

    def one_v_one(self, fmt):
        h, gs = self.started(fmt)
        self.check_start(fmt, h, gs, 2)
        self.concede_to_game_over(h, gs)
        log = open(h.stderr_path, encoding="utf-8", errors="replace").read()
        self.assertIn("nethost: the game started with 2 players (1 online, 0 AI), %s" % fmt, log)
        return h, gs

    def test_brawl_1v1(self):
        self.one_v_one("brawl")

    def test_standard_1v1(self):
        self.one_v_one("standard")

    def test_draft_1v1_with_the_turbo_emblem(self):
        h, _gs = self.one_v_one("draft")
        self.assertEqual(h.rules_used, ("turbo",))
        log = open(h.stderr_path, encoding="utf-8", errors="replace").read()
        self.assertEqual(log.count("has the Turbo Cube emblem"), 2)

    def test_commander_draft_1v1(self):
        self.one_v_one("commander_draft")

    def test_a_pod_with_an_ai_seat_per_format(self):
        for fmt in ("brawl", "standard", "draft", "commander_draft"):
            with self.subTest(fmt=fmt):
                h, gs = self.started(fmt, guests=1, ai=1, seed=11)
                self.check_start(fmt, h, gs, 3)
                ai = [p for p in gs[0].state["players"] if p.get("ai")]
                self.assertEqual(len(ai), 1)
                self.assertTrue(ai[0]["name"].startswith("AI 1"))
                h.close()
                gs[0].close()

    def test_a_deck_of_another_format_is_refused_in_words(self):
        h, port = self.start_host(self.decks["brawl"][0])
        with self.assertRaises(fn.NetRefused) as cm:
            self.guest(port, self.decks["commander"][1])
        self.assertEqual(cm.exception.reason, "deck")
        self.assertIn("This is a Brawl game: choose a Brawl deck (yours is a Commander deck).", str(cm.exception))
        self.assertEqual(h.guests_joined, [])
        g = self.guest(port, self.decks["brawl"][1])                      # the seat is still free for a Brawl deck
        self.assertTrue(wait_for(lambda: h.ready and g.ready, 60, pump=[h, g]))

    def test_standard_and_draft_decks_play_together(self):
        h, port = self.start_host(self.decks["standard"][0])
        g = self.guest(port, self.decks["draft"][1])
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state, 90, pump=[h, g]))
        self.check_start("standard", h, [g], 2)

    def test_the_mulligan_rule(self):
        """103.5c: in a 1v1 Standard game every mulligan costs a card; Brawl's first mulligan is free."""
        for fmt, want in (("standard", ([7, 6, 5], [1, 2])), ("brawl", ([7, 7, 6], [0, 1]))):
            with self.subTest(fmt=fmt):
                h, gs = self.started(fmt, seed=5, pregame=False)
                self.assertEqual(self.mulligan_twice(h, gs[0]), want)
                h.close()
                gs[0].close()

    def mulligan_twice(self, s, other):
        sizes, prompts = [], []
        taken, last, since, t0 = 0, None, time.time(), time.time()
        while time.time() - t0 < 150 and not (s.exited or s.fatal):
            other.poll()
            for r in list(other.requests):
                other.answer(r, True if r["kind"] == "confirm" else list(range(max(r.get("min", 1), 1))))
            om = self.msg(other)
            if other.is_mulligan_prompt() or "coin toss" in om or (other.state or {}).get("prompt", {}).get("ok", {}).get(
                    "label") == "Play":
                other.ok()
            s.poll()
            st = s.state
            if not st or not s.me():
                time.sleep(0.05)
                continue
            msg = st["prompt"]["message"]
            key = (msg, len(s.me()["zones"]["hand"]))
            if key != last:
                last, since = key, time.time()
            if time.time() - since > 0.6:
                since = time.time() + 5
                if "coin toss" in msg or st["prompt"].get("ok", {}).get("label") == "Play":
                    s.ok()
                elif s.is_mulligan_prompt():
                    sizes.append(s.me()["handCount"])
                    if taken < 2:
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
        return sizes, prompts

    def test_hidden_information_stays_hidden_in_a_standard_game(self):
        from tools import online_soak as osk
        osk._load_modules()
        h, gs = self.started("standard", seed=21)
        seats = [osk.Seat("host", h, 21), osk.Seat("guest", gs[0], 22)]
        end = time.time() + 120
        while time.time() < end and ((h.state or {}).get("turn") or 0) < 6 and not h.game_over:
            for st in seats:
                st.step()
            time.sleep(0.02)
        self.assertGreaterEqual((h.state or {}).get("turn") or 0, 3, "the game didn't get going")
        for st in seats:
            self.assertEqual(st.hidden, [], st.label)
            me = st.session.state["me"]
            for p in st.session.state["players"]:
                if p["id"] != me:
                    for c in (p["zones"].get("hand") or []) + (p["zones"].get("library") or []):
                        self.assertTrue(c.get("hidden"), c)
                        self.assertIn(c.get("name"), (None, "Face-down card"), c)
        host_names = osk.deck_names(self.decks["standard"][0]) - osk.BASICS
        guest_log = [e.get("text") or "" for e in gs[0].log]
        self.assertEqual(osk.names_seen_in_log(guest_log, host_names, osk.deck_names(self.decks["standard"][1]),
                                               seats[1].seen), [])

    def test_continue_a_brawl_game(self):
        """MP2b in Brawl: the save keeps the format; the continued game is a Brawl game with the same board."""
        from tools import online_soak as osk
        import online_save
        import replay
        osk._load_modules()
        folder = tempfile.mkdtemp(prefix="save_", dir=self.tmp)
        journal = os.path.join(folder, "cmds.jsonl")
        h, port = self.start_host(self.decks["brawl"][0], seed=31, journal_path=journal)
        g = self.guest(port, self.decks["brawl"][1])
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.seat_list, 60, pump=[h, g]))
        seats = [osk.Seat("host", h, 31), osk.Seat("guest", g, 41)]
        end = time.time() + 240
        while time.time() < end and (((h.state or {}).get("turn") or 0) < 5 or online_save.count_lines(journal) <= 10):
            for st in seats:
                st.step()
            time.sleep(0.02)
        self.assertGreaterEqual((h.state or {}).get("turn") or 0, 5)
        self.assertTrue(osk._wait_for_person([(st.session, st.answered_seq) for st in seats]), "no waiting point")
        before_host = replay.summary(h.state, any_waiting=True)
        guest_seat = h.seat_list[0]
        h.close()
        self.assertTrue(wait_for(lambda: g.exited, 15, pump=[g]))
        g.close()
        src = os.path.join(folder, "cmds_1.jsonl")
        shutil.copyfile(journal, src)
        saved_guest = os.path.join(folder, "guest_2.dck")
        shutil.copyfile(guest_seat["deck"], saved_guest)
        h2, port2 = self.start_host(self.decks["brawl"][0], seed=31, journal_path=journal,
                                    resume={"replay": src, "guests": [(guest_seat["seat"], guest_seat["name"], saved_guest)]})
        self.assertEqual(h2.fmt, "brawl")
        self.assertTrue(wait_for(lambda: not h2.resuming or h2.exited, 300, pump=[h2]), "the play-back never finished")
        self.assertIsNone((h2.replay_result or {}).get("diverged"), h2.replay_result)
        with open(saved_guest, encoding="utf-8") as f:
            g2 = fc.NetSession("127.0.0.1", port2, guest_seat["name"], "pw-mp3a", "guest", f.read(),
                               fingerprint=self.cert.fingerprint, code="guestcode")
        g2.start()
        self.addCleanup(g2.close)
        self.assertEqual(g2.fmt, "brawl")
        self.assertTrue(wait_for(lambda: h2.everyone_back() and g2.ready and g2.state, 60, pump=[h2, g2]))
        self.assertIsNone(g2.fatal)
        self.assertEqual(h2.format_used, "brawl")
        osk._wait_for_person([(h2, None), (g2, None)])
        self.assertEqual(replay.differences(before_host, replay.summary(h2.state, any_waiting=True)), [])


if __name__ == "__main__":
    unittest.main()
