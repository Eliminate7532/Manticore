# SPDX-License-Identifier: GPL-3.0-or-later
"""
deck_loader.py - load a deck from a text export or a deck-site URL (shared by the launchers).
"""
import os

from deck_importer import DeckImportError, import_deck, import_from_text


def load_deck(source):
    """Load a deck from a text-export file or a deck URL.

    Returns (commanders, deck_list). Raises DeckImportError with a readable message.
    """
    if source.lower().startswith(("http://", "https://")):
        return import_deck(source)
    path = os.path.abspath(os.path.expanduser(source))
    if not os.path.isfile(path):
        raise DeckImportError(f"Deck file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:   # utf-8-sig tolerates a Notepad BOM
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise DeckImportError(f"Could not read {path}: {e}")
    return import_from_text(text)


def describe_deck(label, commanders, deck):
    """Print a one-line summary and warn if the size is not a legal Commander deck."""
    names = " + ".join(commanders) if commanders else "(no commander found)"
    print(f"{label}: {names} | {len(deck)} cards in library")
    if not commanders:
        print(f"  Warning: no commander detected for {label}; the command zone will be empty.")
    if len(deck) + len(commanders) != 100:
        print(f"  Warning: expected 100 cards including the commander, got {len(deck) + len(commanders)}.")
