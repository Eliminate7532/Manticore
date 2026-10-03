# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for forge_scripts.py: parsing Forge card scripts, costs, "valid card" expressions, file names
and the on-disk cache. Offline: the scripts are verbatim copies saved in tests/fixtures/forge/
(Forge is GPL-3.0; see that folder's README.txt) and downloads are faked.
    python -m unittest discover -s tests -v
"""
import glob
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import requests

import forge_scripts as fs

FIXTURES = os.path.join(HERE, "fixtures", "forge")


def saved_scripts():
    return [p for p in glob.glob(os.path.join(FIXTURES, "*.txt")) if not os.path.basename(p).startswith("README")]


def script(name):
    with open(os.path.join(FIXTURES, name + ".txt"), encoding="utf-8") as f:
        return fs.parse_script(f.read())


class ParseTests(unittest.TestCase):
    def test_fetchland_script(self):
        face = script("wooded_foothills").face()
        self.assertEqual(face.name, "Wooded Foothills")
        self.assertEqual(face.types, ["Land"])
        self.assertTrue(face.is_land)
        ab = face.abilities[0]
        self.assertEqual((ab["AB"], ab["Origin"], ab["Destination"]), ("ChangeZone", "Library", "Battlefield"))
        self.assertEqual(ab["Cost"], "T PayLife<1> Sac<1/CARDNAME>")
        self.assertEqual(ab["ChangeType"], "Mountain,Forest")

    def test_creature_fields(self):
        face = script("birds_of_paradise").face()
        self.assertEqual((face.mana_cost, face.pt, face.keywords), ("G", "0/1", ["Flying"]))
        self.assertEqual(face.card_types, ["Creature"])

    def test_oracle_newlines_are_real_newlines(self):
        oracle = script("breeding_pool").face().oracle
        self.assertEqual(oracle.split("\n")[0], "({T}: Add {G} or {U}.)")
        self.assertEqual(len(oracle.split("\n")), 2)

    def test_two_faced_card_has_two_faces(self):
        card = script("barkchannel_pathway_tidechannel_pathway")
        self.assertEqual([f.name for f in card.faces], ["Barkchannel Pathway", "Tidechannel Pathway"])
        self.assertEqual(card.face(0).alternate_mode, "Modal")
        self.assertEqual(card.face(1).abilities[0]["Produced"], "U")
        self.assertEqual(card.face(5).name, "Tidechannel Pathway")       # out-of-range index = last face

    def test_spell_front_land_back(self):
        card = script("sink_into_stupor_soporific_springs")
        self.assertFalse(card.face(0).is_land)
        self.assertTrue(card.face(1).is_land)
        self.assertEqual(card.face(1).svar_params("DBTap")["UnlessCost"], "PayLife<3>")

    def test_triggers_replacements_and_svars(self):
        mox = script("chrome_mox").face()
        self.assertEqual(mox.triggers[0]["Mode"], "ChangesZone")
        self.assertEqual(mox.svar_params("TrigExile")["ChangeType"], "Card.nonArtifact+nonLand")
        self.assertIsNone(mox.svar_params("NeedsToPlayVar"))            # 'Z GE1' is not an ability
        vault = script("mana_vault").face()
        self.assertEqual(vault.replacements[0]["Event"], "Untap")

    def test_comments_and_blank_lines_are_skipped(self):
        card = script("gemstone_caverns")
        self.assertEqual(card.face().name, "Gemstone Caverns")

    def test_bom_and_windows_line_endings_are_tolerated(self):
        card = fs.parse_script("\ufeffName:Test Card\r\nTypes:Creature Elf\r\nA:AB$ Mana | Cost$ T | Produced$ G\r\n")
        self.assertEqual((card.name, card.face().abilities[0]["Produced"]), ("Test Card", "G"))

    def test_not_a_script_is_rejected(self):
        for junk in ("", "   \n", "<html><body>404: Not Found</body></html>", "404: Not Found"):
            with self.assertRaises(ValueError, msg=junk):
                fs.parse_script(junk)

    def test_every_saved_script_parses(self):
        files = saved_scripts()
        self.assertGreater(len(files), 30)
        for path in files:
            with open(path, encoding="utf-8") as f:
                card = fs.parse_script(f.read())
            self.assertTrue(card.name, path)


class CostTests(unittest.TestCase):
    def test_tap_life_sacrifice(self):
        c = fs.parse_cost("T PayLife<1> Sac<1/CARDNAME>")
        self.assertTrue(c.tap and c.sac_self)
        self.assertEqual((c.life, c.mana, c.unsupported), (1, {}, []))

    def test_mana_symbols(self):
        c = fs.parse_cost("5 G U")
        self.assertEqual(c.mana, {"generic": 5, "G": 1, "U": 1})
        self.assertFalse(c.tap)

    def test_tap_another_permanent(self):
        c = fs.parse_cost("T tapXType<1/Creature>")
        self.assertEqual(c.tap_other, (1, "Creature"))

    def test_hand_costs(self):
        self.assertTrue(fs.parse_cost("ExileFromHand<1/CARDNAME>").exile_self_from_hand)
        self.assertTrue(fs.parse_cost("Discard<1/CARDNAME>").discard_self)

    def test_unknown_parts_are_reported_not_ignored(self):
        c = fs.parse_cost("T Exile<1/Creature>")
        self.assertEqual(c.unsupported, ["Exile<1/Creature>"])
        self.assertEqual(fs.parse_cost("X T").unsupported, ["X"])

    def test_every_saved_ability_cost_is_understood(self):
        for path in saved_scripts():
            with open(path, encoding="utf-8") as f:
                card = fs.parse_script(f.read())
            for face in card.faces:
                for ab in face.abilities:
                    self.assertEqual(fs.parse_cost(ab.get("Cost", "")).unsupported, [], (face.name, ab.get("Cost")))


class ValidExpressionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(fs.compare(3, "GT2"))
        self.assertFalse(fs.compare(2, "GT2"))
        self.assertTrue(fs.compare(2, "GE2") and fs.compare(1, "LT2") and fs.compare(0, "EQ0") and fs.compare(5, "NE4"))
        self.assertTrue(fs.compare(1, ""))            # default GE1
        with self.assertRaises(fs.ForgeUnsupported):
            fs.compare(1, "MAYBE")

    def test_type_words(self):
        self.assertEqual(fs.type_words("Legendary Creature — Human Druid"), {"Legendary", "Creature", "Human", "Druid"})
        self.assertEqual(fs.type_words("Instant // Land"), {"Instant", "Land"})
        self.assertEqual(fs.type_words("Basic Land — Forest"), {"Basic", "Land", "Forest"})
        self.assertEqual(fs.type_words(None), set())

    def match(self, expr, type_line, **kw):
        return fs.matches_valid(expr, fs.type_words(type_line), **kw)

    def test_land_types_for_fetching(self):
        self.assertTrue(self.match("Mountain,Forest", "Land — Forest Island"))       # Breeding Pool style
        self.assertFalse(self.match("Mountain,Forest", "Land — Island"))
        self.assertFalse(self.match("Mountain,Forest", "Land"))
        self.assertFalse(self.match("Mountain,Forest", "Creature — Elf"))

    def test_basic_land(self):
        self.assertTrue(self.match("Land.Basic", "Basic Land — Forest"))
        self.assertFalse(self.match("Land.Basic", "Land — Forest Island"))
        self.assertFalse(self.match("Land.Basic", "Basic Snow Artifact"))

    def test_qualifiers(self):
        legend = "Legendary Creature — Human Druid"
        self.assertTrue(self.match("Creature.Legendary+YouCtrl,Planeswalker.Legendary+YouCtrl", legend))
        self.assertFalse(self.match("Creature.Legendary+OppCtrl", legend))
        self.assertTrue(self.match("Creature.Legendary+OppCtrl", legend, controller="opp"))
        self.assertFalse(self.match("Creature.nonHuman", legend))
        self.assertTrue(self.match("Creature.nonHuman", "Creature — Elf Druid"))
        self.assertTrue(self.match("Card.nonArtifact+nonLand", "Creature — Elf", colors={"G"}))
        self.assertFalse(self.match("Card.nonArtifact+nonLand", "Artifact"))
        self.assertFalse(self.match("Card.nonArtifact+nonLand", "Land"))
        self.assertTrue(self.match("Permanent.nonLand", "Artifact"))
        self.assertFalse(self.match("Permanent.nonLand", "Land — Forest"))

    def test_colours_and_state(self):
        self.assertTrue(self.match("Creature.Green", "Creature — Elf", colors={"G"}))
        self.assertFalse(self.match("Creature.nonGreen", "Creature — Elf", colors={"G"}))
        self.assertTrue(self.match("Card.Colorless", "Artifact"))
        self.assertTrue(self.match("Card.nonColorless", "Creature", colors={"U"}))
        self.assertTrue(self.match("Land.tapped", "Land", tapped=True))
        self.assertFalse(self.match("Land.untapped", "Land", tapped=True))
        self.assertFalse(self.match("Creature.Other", "Creature", is_self=True))

    def test_unknown_things_raise_instead_of_guessing(self):
        for expr in ("Creature.cmcGE3", "Creature.withFlying", "Creature.Token", "Defined.Imprinted", "Creature.Chosen"):
            with self.assertRaises(fs.ForgeUnsupported, msg=expr):
                self.match(expr, "Creature")


class FileNameTests(unittest.TestCase):
    def test_names(self):
        cases = {
            "Kinnan, Bonder Prodigy": "kinnan_bonder_prodigy",
            "Sol Ring": "sol_ring",
            "Urza's Saga": "urzas_saga",
            "Urza’s Saga": "urzas_saga",
            "Barkchannel Pathway // Tidechannel Pathway": "barkchannel_pathway_tidechannel_pathway",
            "Barkchannel Pathway / Tidechannel Pathway": "barkchannel_pathway_tidechannel_pathway",
            "Fire // Ice": "fire_ice",
            "Otawara, Soaring City": "otawara_soaring_city",
            "Boseiju, Who Endures": "boseiju_who_endures",
            "Mox Opal": "mox_opal",
            "Lim-Dûl's Vault": "lim_duls_vault",
            "Æther Vial": "aether_vial",
            "Jötun Grunt": "jotun_grunt",
        }
        for name, expected in cases.items():
            self.assertEqual(fs.script_file_name(name), expected, name)

    def test_url(self):
        self.assertTrue(fs.script_url("wooded_foothills").endswith("/cardsfolder/w/wooded_foothills.txt"))


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, files=None, error=None):
        self.files, self.error, self.calls = files or {}, error, []
        self.headers = {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.error:
            raise self.error
        name = url.rsplit("/", 1)[1][:-4]
        if name in self.files:
            return FakeResponse(200, self.files[name])
        return FakeResponse(404)


def raw(name):
    with open(os.path.join(FIXTURES, name + ".txt"), encoding="utf-8") as f:
        return f.read()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def store(self, session=None):
        st = fs.ForgeScriptStore(self.tmp.name)
        if session is not None:
            st._session = session
        return st

    def test_peek_reads_disk_only_and_never_downloads(self):
        session = FakeSession({"sol_ring": raw("sol_ring")})
        st = self.store(session)
        self.assertIsNone(st.peek("Sol Ring"))
        self.assertEqual(session.calls, [])

    def test_fetch_downloads_saves_and_then_peek_works_offline(self):
        st = self.store(FakeSession({"sol_ring": raw("sol_ring")}))
        card = st.fetch("Sol Ring")
        self.assertEqual(card.name, "Sol Ring")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "forge", "sol_ring.txt")))
        again = self.store(FakeSession(error=requests.ConnectionError("offline")))     # a new run with no network
        self.assertEqual(again.peek("Sol Ring").name, "Sol Ring")

    def test_second_fetch_uses_memory_not_the_network(self):
        session = FakeSession({"sol_ring": raw("sol_ring")})
        st = self.store(session)
        st.fetch("Sol Ring")
        st.fetch("sol ring")
        self.assertEqual(len(session.calls), 1)

    def test_two_faced_card_is_found_under_every_face_name(self):
        name = "barkchannel_pathway_tidechannel_pathway"
        st = self.store(FakeSession({name: raw(name)}))
        card = st.fetch("Barkchannel Pathway // Tidechannel Pathway")
        self.assertEqual(st.peek("Tidechannel Pathway"), card)
        self.assertEqual(st.peek("Barkchannel Pathway").face(1).name, "Tidechannel Pathway")

    def test_front_face_name_is_tried_when_the_full_name_is_missing(self):
        session = FakeSession({"sol_ring": raw("sol_ring")})
        st = self.store(session)
        self.assertEqual(st.fetch("Sol Ring // Something Else").name, "Sol Ring")
        self.assertEqual(len(session.calls), 2)

    def test_404_is_remembered_and_not_asked_again(self):
        session = FakeSession()
        st = self.store(session)
        self.assertIsNone(st.fetch("Nonexistent Card"))
        self.assertIsNone(st.fetch("Nonexistent Card"))
        self.assertEqual(len(session.calls), 1)
        later = self.store(session)                                  # remembered across runs too
        self.assertIsNone(later.fetch("Nonexistent Card"))
        self.assertEqual(len(session.calls), 1)

    def test_old_404_is_retried_after_a_week(self):
        session = FakeSession()
        st = self.store(session)
        st.fetch("Nonexistent Card")
        st._missing["nonexistent_card"] = time.time() - fs.MISSING_RETRY_SECONDS - 5
        st.cards.clear()
        st.fetch("Nonexistent Card")
        self.assertEqual(len(session.calls), 2)

    def test_network_failure_pauses_downloads_and_reports(self):
        session = FakeSession(error=requests.ConnectionError("no route"))
        st = self.store(session)
        self.assertIsNone(st.fetch("Sol Ring"))
        self.assertIn("download failed", st.last_error)
        self.assertTrue(st.offline)
        self.assertIsNone(st.fetch("Mana Vault"))
        self.assertEqual(len(session.calls), 1)                       # did not hammer a dead network

    def test_html_error_page_is_not_saved_as_a_script(self):
        st = self.store(FakeSession({"sol_ring": "<html>Rate limited</html>"}))
        self.assertIsNone(st.fetch("Sol Ring"))
        self.assertIn("could not be parsed", st.last_error)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "forge", "sol_ring.txt")))

    def test_prefetch_counts_what_is_available(self):
        st = self.store(FakeSession({"sol_ring": raw("sol_ring"), "mana_vault": raw("mana_vault")}))
        self.assertEqual(st.prefetch(["Sol Ring", "Mana Vault", "Nonexistent Card"]), 2)

    def test_corrupt_cache_file_is_ignored(self):
        os.makedirs(os.path.join(self.tmp.name, "forge"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "forge", "sol_ring.txt"), "w", encoding="utf-8") as f:
            f.write("<html>oops</html>")
        st = self.store()
        self.assertIsNone(st.peek("Sol Ring"))
        self.assertIn("Could not read cached Forge script", st.last_error)


class CardStoreIntegrationTests(unittest.TestCase):
    """CardDataStore.peek_forge / prefetch_forge use Scryfall's full name so two-faced cards find their file."""

    def make_store(self, files):
        import card_data
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = card_data.CardDataStore()
        store.cards = {"barkchannel pathway / tidechannel pathway":
                       {"name": "Barkchannel Pathway // Tidechannel Pathway"},
                       "sol ring": {"name": "Sol Ring"}}
        store.forge = fs.ForgeScriptStore(tmp.name)
        store.forge._session = FakeSession(files)
        return store

    def test_prefetch_then_peek_uses_the_scryfall_name(self):
        name = "barkchannel_pathway_tidechannel_pathway"
        store = self.make_store({name: raw(name), "sol_ring": raw("sol_ring"), "mana_vault": raw("mana_vault")})
        names = ["Barkchannel Pathway / Tidechannel Pathway", "Sol Ring", "Mana Vault"]
        self.assertEqual(store.peek_forge(names[0]), None)                        # nothing downloaded yet
        self.assertEqual(store.prefetch_forge(names), (3, 3))
        self.assertEqual(store.peek_forge(names[0]).face(1).name, "Tidechannel Pathway")
        self.assertEqual(store.peek_forge("Sol Ring").name, "Sol Ring")
        self.assertIn(name + ".txt", os.listdir(store.forge.dir))

    def test_missing_scripts_are_counted(self):
        store = self.make_store({"sol_ring": raw("sol_ring")})
        self.assertEqual(store.prefetch_forge(["Sol Ring", "Mana Vault"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
