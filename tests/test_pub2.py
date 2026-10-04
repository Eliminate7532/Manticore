# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 35 (3 Oct 2026): Karl made the public repository, github.com/Eliminate7532/Manticore, from the 0.28.38 export.
update_config.json names it (installed copies look for updates in its Releases), and licenses/NOTICES.json's source_url
points the Licenses and credits window - and a --release installer build - at it."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import licenses_view

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "Eliminate7532/Manticore"                 # the case GitHub uses (Karl created it with a capital M)


def load(rel):
    with open(os.path.join(BASE_DIR, rel), encoding="utf-8") as f:
        return json.load(f)


class PublicRepositoryTests(unittest.TestCase):
    def test_update_config_and_the_source_link_name_the_same_repository(self):
        self.assertEqual(load("update_config.json")["repo"], REPO)
        self.assertEqual(load("licenses/NOTICES.json")["project"]["source_url"], "https://github.com/" + REPO)

    def test_the_about_page_points_at_the_repository(self):
        lines = [text for text, _colour, _bold in licenses_view._about_lines(load("licenses/NOTICES.json"), lambda: "Manticore x")]
        self.assertIn("https://github.com/" + REPO, lines)
        offer = next(t for t in lines if t.startswith("This program is free software licensed"))
        self.assertTrue(offer.endswith("get it from the project's repository:"), offer)
        self.assertNotIn("once published", offer)

    def test_without_a_source_link_the_old_wording_stays(self):
        lines = [text for text, _c, _b in licenses_view._about_lines({"project": {}}, lambda: "x")]
        offer = next(t for t in lines if t.startswith("This program is free software licensed"))
        self.assertIn("ask Karl for it, or (once published)", offer)
        self.assertFalse(any(t.startswith("https://") for t in lines))


if __name__ == "__main__":
    unittest.main()
