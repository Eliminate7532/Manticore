# SPDX-License-Identifier: GPL-3.0-or-later
"""Test helper: a card store that answers peek_forge from the saved Forge scripts in fixtures/forge."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from forge_scripts import ForgeScriptStore


class ForgeStoreMixin:
    """Add to a store that has peek_card(name). Scripts are read from tests/fixtures/forge/ (no network)."""

    def _forge_store(self):
        if not hasattr(self, "_forge_scripts"):
            self._forge_scripts = ForgeScriptStore(os.path.join(HERE, "fixtures"))
        return self._forge_scripts

    def peek_forge(self, name):
        info = self.peek_card(name)
        return self._forge_store().peek(info["name"] if info else name)
