# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_fake.py - a ForgeSession that never starts Java: it shows saved snapshots and records what the GUI sends.
Used by the GUI tests (and handy for taking screenshots).
"""
import json
import os

from card_data import CardDataStore
from forge_client import ForgeSession

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "forge_states")


def load_state(name):
    with open(os.path.join(FIXTURES, name + ".json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_log():
    with open(os.path.join(FIXTURES, "log_sample.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class FakeSession(ForgeSession):
    def __init__(self, state=None, log=None):
        super().__init__("deck.dck", [])
        self.sent = []
        self.ready = True
        if state:
            self.handle(state)
        if log:
            self.handle({"t": "log", "entries": log})

    def alive(self):
        return True

    def send(self, **cmd):
        self.sent.append(cmd)
        return True

    def commands(self, kind=None):
        return [c for c in self.sent if kind is None or c["c"] == kind]


class StubStore:
    """Card pictures from the local cache only; never touches the network."""
    last_error = None

    def prefetch_cards(self, names):
        return 0

    def peek_image_path(self, name, size="normal"):
        path = CardDataStore._image_file(name, size)
        return str(path) if path.exists() else None

    def get_image_path(self, name, size="normal"):
        return self.peek_image_path(name, size)
