# SPDX-License-Identifier: GPL-3.0-or-later
"""ForgeSession, tested against a stand-in bridge (no Java needed) plus the Java/runtime checks."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_bridge.py")


def wait_for(session, cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        session.poll()
        if cond():
            return True
        time.sleep(0.01)
    return False


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.s = fc.ForgeSession("deck.dck", [], command=[sys.executable, FAKE])
        self.s.stderr_path = os.path.join(tempfile.gettempdir(), "forge_client_test.log")
        self.s.start()
        self.addCleanup(self.s.close)

    def test_receives_ready_state_and_request(self):
        self.assertTrue(wait_for(self.s, lambda: self.s.state and self.s.requests))
        self.assertTrue(self.s.ready)
        self.assertEqual(self.s.me()["name"], "Me")
        self.assertEqual([p["name"] for p in self.s.opponents()], ["AI"])
        self.assertEqual(self.s.prompt_text(), "Priority")
        self.assertEqual(self.s.card(9)["name"], "Island")

    def test_commands_reach_the_bridge_as_json(self):
        wait_for(self.s, lambda: self.s.state)
        self.s.click_card(5)
        self.s.click_player(1)
        self.s.ok()
        self.s.set_stops(True, ["MAIN1", "MAIN2"])
        self.assertTrue(wait_for(self.s, lambda: len(self.s.log) >= 4))
        texts = [e["text"] for e in self.s.log]
        self.assertIn('got {"c": "card", "id": 5}', texts)
        self.assertIn('got {"c": "player", "id": 1}', texts)
        self.assertIn('got {"c": "ok"}', texts)
        self.assertIn('got {"c": "stops", "mine": true, "phases": ["MAIN1", "MAIN2"]}', texts)
        self.assertEqual(self.s.log_total, 4)

    def test_answering_a_request_sends_reply_and_removes_it(self):
        wait_for(self.s, lambda: self.s.requests)
        req = self.s.requests[0]
        self.s.answer(req, True)
        self.assertEqual(len(self.s.requests), 0)
        self.assertTrue(wait_for(self.s, lambda: self.s.log))
        self.assertIn('"c": "reply"', self.s.log[0]["text"])
        self.assertIn('"id": 1', self.s.log[0]["text"])

    def test_fatal_and_exit_are_reported(self):
        wait_for(self.s, lambda: self.s.state)
        self.s.send(c="die")
        self.assertTrue(wait_for(self.s, lambda: self.s.fatal and self.s.exited))
        self.assertEqual(self.s.fatal, "boom")
        self.assertFalse(self.s.alive())
        self.assertFalse(self.s.send(c="ok"))

    def test_record_file_gets_every_message(self):
        path = os.path.join(tempfile.gettempdir(), "forge_client_rec.jsonl")
        s = fc.ForgeSession("d", [], command=[sys.executable, FAKE], record_path=path)
        s.stderr_path = os.path.join(tempfile.gettempdir(), "forge_client_test2.log")
        s.start()
        wait_for(s, lambda: s.requests)
        s.close()
        with open(path, encoding="utf-8") as f:
            kinds = [line.split('"t": "')[1].split('"')[0] for line in f if '"t"' in line]
        self.assertEqual(kinds[:3], ["ready", "state", "request"])

    def test_log_is_capped_but_total_keeps_counting(self):
        for i in range(2100):
            self.s.handle({"t": "log", "entries": [{"type": "X", "text": str(i)}]})
        self.assertEqual(len(self.s.log), 2000)
        self.assertEqual(self.s.log_total, 2100)
        self.assertEqual(self.s.log[-1]["text"], "2099")


class DeckFileTests(unittest.TestCase):
    def make_runtime(self, tmp):
        """A pretend forge_runtime with just the card scripts that decide how a two-faced card is named."""
        scripts = {"fire_ice": "Name:Fire\nManaCost:1 R\nTypes:Instant\nAlternateMode:Split\n\nALTERNATE\n\nName:Ice\n",
                   "barkchannel_pathway_tidechannel_pathway": "Name:Barkchannel Pathway\nTypes:Land\nAlternateMode:Modal\n\nALTERNATE\n\nName:Tidechannel Pathway\n",
                   "delver_of_secrets_insectile_aberration": "Name:Delver of Secrets\nAlternateMode:DoubleFaced\n\nALTERNATE\n",
                   "bonecrusher_giant_stomp": "Name:Bonecrusher Giant\nAlternateMode:Adventure\n\nALTERNATE\n"}
        for slug, text in scripts.items():
            folder = os.path.join(tmp, "res", "cardsfolder", slug[0])
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, slug + ".txt"), "w", encoding="utf-8") as f:
                f.write(text)
        return tmp

    def test_forge_card_name_split_cards_keep_both_names_every_other_two_faced_card_uses_the_front(self):
        """Reported from Karl's PC: Forge dropped 'Barkchannel Pathway // Tidechannel Pathway' (unknown to its database)."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = self.make_runtime(tmp)
            self.assertEqual(fc.forge_card_name("Fire // Ice", rt), "Fire // Ice")
            self.assertEqual(fc.forge_card_name("Fire / Ice", rt), "Fire // Ice")
            self.assertEqual(fc.forge_card_name("Barkchannel Pathway / Tidechannel Pathway", rt), "Barkchannel Pathway")
            self.assertEqual(fc.forge_card_name("Barkchannel Pathway // Tidechannel Pathway", rt), "Barkchannel Pathway")
            self.assertEqual(fc.forge_card_name("Delver of Secrets / Insectile Aberration", rt), "Delver of Secrets")
            self.assertEqual(fc.forge_card_name("Bonecrusher Giant // Stomp", rt), "Bonecrusher Giant")
            self.assertEqual(fc.forge_card_name("Sol Ring", rt), "Sol Ring")
            self.assertEqual(fc.forge_card_name("  Kinnan, Bonder Prodigy ", rt), "Kinnan, Bonder Prodigy")

    def test_forge_knows_a_two_faced_card_by_its_front_face_alone(self):
        """Karl's PC (round 15): pasting a deck with 'Blightstep Pathway' (just the front face, the way every deck
        site writes an MDFC land) got the reply 'Forge does not know: Blightstep Pathway', even though Forge's own
        script for it declares exactly that Name: line. The old check guessed a file name from the pasted text
        (blightstep_pathway.txt), but Forge's actual file for a two-faced card is named after BOTH faces joined
        together (barkchannel_pathway_tidechannel_pathway.txt here) - a false alarm, not a real gap."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = self.make_runtime(tmp)
            for name in ("Barkchannel Pathway", "Tidechannel Pathway", "Delver of Secrets", "Bonecrusher Giant", "Fire", "Ice"):
                self.assertTrue(fc.forge_knows(name, rt), f"{name} should be recognised from its own Name: line")
            self.assertEqual(fc.unknown_cards(["Barkchannel Pathway", "Delver of Secrets", "Not A Real Card"], rt),
                             ["Not A Real Card"])

    def test_a_two_faced_card_without_a_script_falls_back_to_its_front_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fc.forge_card_name("Some Front / Some Back", tmp), "Some Front")

    def test_deck_file_lists_two_faced_cards_by_front_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self.make_runtime(tmp)
            path = fc.write_deck_file(os.path.join(tmp, "d.dck"), ["Kinnan, Bonder Prodigy"],
                                      ["Barkchannel Pathway / Tidechannel Pathway", "Fire // Ice", "Forest", "Forest"], "T", rt)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("1 Barkchannel Pathway\n", text)
            self.assertNotIn("Tidechannel", text)
            self.assertIn("1 Fire // Ice", text)
            self.assertIn("2 Forest", text)

    def test_write_deck_file_counts_duplicates_and_marks_commander(self):
        with tempfile.TemporaryDirectory() as d:
            path = fc.write_deck_file(os.path.join(d, "sub", "x.dck"), ["Kinnan, Bonder Prodigy"],
                                      ["Forest", "Forest", "Sol Ring"], "Test")
            with open(path, encoding="utf-8") as f:
                text = f.read().splitlines()
        self.assertEqual(text, ["[metadata]", "Name=Test", "[Commander]", "1 Kinnan, Bonder Prodigy", "[Main]",
                                "2 Forest", "1 Sol Ring"])


class RuntimeCheckTests(unittest.TestCase):
    def test_missing_java_is_reported_in_plain_words(self):
        with mock.patch.object(fc, "find_java", return_value=None):
            problem = fc.runtime_problem(tempfile.gettempdir())
        self.assertIn("Java", problem)
        self.assertIn("adoptium", problem)

    def test_missing_jars_point_to_setup(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(fc, "find_java", return_value="java"):
            self.assertIn("setup_forge.py", fc.runtime_problem(d))
            for name in ("forge.jar", "forge_bridge.jar"):
                open(os.path.join(d, name), "w").close()
            self.assertIn("card scripts", fc.runtime_problem(d))
            os.makedirs(os.path.join(d, "res", "cardsfolder"))
            self.assertIsNone(fc.runtime_problem(d))

    def test_start_without_runtime_raises_forge_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            s = fc.ForgeSession("d", [], runtime=d)
            with mock.patch.object(fc, "find_java", return_value=None):
                with self.assertRaises(fc.ForgeUnavailable):
                    s.start()

    def test_java_major_parses_old_and_new_version_strings(self):
        def fake(out):
            return mock.Mock(stderr=out)
        with mock.patch.object(fc.subprocess, "run", return_value=fake('openjdk version "21.0.2" 2024-01-16')):
            self.assertEqual(fc.java_major("java"), 21)
        with mock.patch.object(fc.subprocess, "run", return_value=fake('java version "1.8.0_301"')):
            self.assertEqual(fc.java_major("java"), 8)
        with mock.patch.object(fc.subprocess, "run", return_value=fake('java version "17"')):
            self.assertEqual(fc.java_major("java"), 17)
        with mock.patch.object(fc.subprocess, "run", return_value=fake("garbage")):
            self.assertEqual(fc.java_major("java"), 0)


class BridgeSyncTests(unittest.TestCase):
    def test_a_newer_bridge_jar_replaces_the_old_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "runtime")
            os.makedirs(runtime)
            src = os.path.join(tmp, "forge_bridge.jar")
            with open(src, "wb") as f:
                f.write(b"new bridge")
            self.assertTrue(fc.sync_bridge(runtime, src))                     # none there yet
            with open(os.path.join(runtime, "forge_bridge.jar"), "rb") as f:
                self.assertEqual(f.read(), b"new bridge")
            self.assertFalse(fc.sync_bridge(runtime, src))                    # identical: nothing to do
            with open(os.path.join(runtime, "forge_bridge.jar"), "wb") as f:
                f.write(b"old bridge")
            self.assertTrue(fc.sync_bridge(runtime, src))                     # different: replaced
            with open(os.path.join(runtime, "forge_bridge.jar"), "rb") as f:
                self.assertEqual(f.read(), b"new bridge")

    def test_missing_pieces_are_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fc.sync_bridge(os.path.join(tmp, "nope"), os.path.join(tmp, "x.jar")))
            self.assertFalse(fc.sync_bridge(tmp, os.path.join(tmp, "missing.jar")))


class ClassPathTests(unittest.TestCase):
    def test_the_bridge_comes_before_forge_so_its_patched_mulligan_class_wins(self):
        cp = fc.class_path(os.path.join("rt"))
        first, second = cp.split(os.pathsep)
        self.assertTrue(first.endswith("forge_bridge.jar"))
        self.assertTrue(second.endswith("forge.jar"))

    def test_the_shipped_bridge_carries_the_free_mulligan_class(self):
        import zipfile
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            names = set(z.namelist())
        self.assertIn("forge/bridge/Main.class", names)
        self.assertIn("forge/game/mulligan/MulliganService.class", names)

    def test_the_patched_source_grants_every_player_a_free_first_mulligan(self):
        src = os.path.join(os.path.dirname(fc.BRIDGE_SOURCE), "src", "forge", "game", "mulligan", "MulliganService.java")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("boolean firstMullFree = true;", text)


if __name__ == "__main__":
    unittest.main()
