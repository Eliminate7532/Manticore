# SPDX-License-Identifier: GPL-3.0-or-later
"""
rules_report.py - shows how the simulator understands every card of a deck.

Usage (from the project folder):
    python rules_report.py                     the bundled Kinnan sample deck
    python rules_report.py my_deck.txt         a text export
    python rules_report.py https://archidekt.com/decks/...

It downloads the Scryfall data and the Forge card scripts for the deck (the first run takes a
minute; after that everything comes from the cache folder), then lists for each card where its
rules come from and what the simulator will do: enter tapped or untapped, fetch what, tap for
which mana. Wherever the Forge script and the Oracle text disagree it says DIFFERENT.
The report is also saved to rules_report.txt. Nothing here starts the game.
"""
import os
import sys

import forge_rules as fr
import game_actions as ga
import paths
from game_state import GameState, Player
from mana_system import card_face

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DECK = os.path.join(paths.sample_dir(), "stompy_goreclaw.txt")      # patch 38 (was the Kinnan sample)
REPORT_FILE = paths.log_file("rules_report.txt")     # round 28: moves to local_dir()/logs in an installed copy


class _TextOnly:
    """The same store with Forge switched off: the rules come from the Scryfall text."""
    def __init__(self, store):
        self.peek_card = store.peek_card


def _scenario(store, commanders, lands=0, opponents=1):
    me = Player("Me", ["Forest"] * 10, commanders=list(commanders))
    gs = GameState(me, Player("Opp", ["Island"] * 10))
    for i in range(opponents - 1):
        gs.players.append(Player(f"Opp{i + 2}", ["Island"] * 10))
    for _ in range(lands):
        me.battlefield.append({"name": "Forest", "face": 0, "tapped": False, "counters": {},
                               "summoning_sick": False, "is_land": True})
    return gs, me


def _mana_key(o):
    return (tuple(sorted(o.produces.items())), o.life_cost, o.damage, o.sacrifice, o.condition)


def _entry_summary(store, commanders, name, face, lands, opponents):
    gs, me = _scenario(store, commanders, lands, opponents)
    entry, note = ga.enter_battlefield(gs, me, name, store, face=face)
    aw = entry.get("awaiting")
    if aw and aw["kind"] == "pay_or_tap":
        return f"asks: pay {aw['life']} life or enter tapped"
    if aw:
        return f"asks: {aw['kind']}"
    return "enters tapped" if entry["tapped"] else "enters untapped"


def describe_card(store, commanders, name):
    """(source, [detail lines], [problems]) for one card, comparing the Forge reading with the text reading."""
    text_store = _TextOnly(store)
    info = store.peek_card(name)
    if info is None:
        return "no Scryfall data", ["not found on Scryfall (check the spelling in the deck list)"], []
    has_script = store.peek_forge(name) is not None if hasattr(store, "peek_forge") else False
    source = "Forge script" if has_script else "card text only"
    faces = len(info.get("card_faces") or [0]) if info.get("layout") == "modal_dfc" else 1
    details, problems = [], []
    for face in range(faces):
        fname = card_face(info, face).get("name") or name
        prefix = f"{fname}: " if faces > 1 else ""
        type_line = card_face(info, face).get("type_line") or ""
        entry = {"name": name, "face": face, "counters": {}}

        if "Land" in type_line:
            for lands, opps in ((0, 1), (3, 1), (3, 2)):
                a = _entry_summary(store, commanders, name, face, lands, opps)
                b = _entry_summary(text_store, commanders, name, face, lands, opps)
                if a != b:
                    problems.append(f"{prefix}entering with {lands} other lands, {opps} opponent(s): "
                                    f"Forge says '{a}', card text says '{b}'")
            first = _entry_summary(store, commanders, name, face, 0, 1)
            later = _entry_summary(store, commanders, name, face, 3, 1)
            details.append(prefix + (first if first == later else f"{first} with 0-2 other lands, {later} with more"))

        gs, me = _scenario(store, commanders)
        spec = ga.fetch_spec(store, dict(entry, tapped=False))
        text_spec = ga.fetch_spec(text_store, dict(entry, tapped=False))
        if spec or text_spec:
            if spec:
                details.append(f"{prefix}fetchland: {('pay ' + str(spec['life']) + ' life, ') if spec['life'] else ''}"
                               f"search for {spec['what']}{' (tapped)' if spec['tapped'] else ''}")
            if bool(spec) != bool(text_spec) or (spec and text_spec and (spec["life"], spec["tapped"]) != (text_spec["life"], text_spec["tapped"])):
                problems.append(f"{prefix}fetchland reading differs: Forge {spec}, card text {text_spec}")

        forge_opts, forge_manual, forge_inactive, forge_hand = ga._mana_abilities(gs, me, entry, store)
        text_opts, text_manual, _i, _h = ga._mana_abilities(gs, me, entry, text_store)
        if forge_opts:
            shown = "; ".join(sorted({o.label() for o in forge_opts}))
            details.append(f"{prefix}taps for {shown}")
        for h in forge_hand:
            details.append(f"{prefix}from hand: {h.label()}")
        for line in forge_inactive:
            details.append(f"{prefix}mana ability that depends on the game state: {line}")
        for line in forge_manual:
            details.append(f"{prefix}mana ability NOT automated (use the 1-6 keys): {line}")
        if has_script and forge_opts and text_opts and not text_manual:
            if sorted(map(_mana_key, forge_opts)) != sorted(map(_mana_key, text_opts)):
                problems.append(f"{prefix}mana differs: Forge {sorted({o.label() for o in forge_opts})}, "
                                f"card text {sorted({o.label() for o in text_opts})}")
        gs, me = _scenario(store, commanders)
        ff = fr.face_of(store, name, face)
        if ff is not None:
            prompt = fr.entry_prompt(ff)
            if prompt:
                details.append(f"{prefix}asks a question when it enters ({prompt['kind'].replace('_', ' ')})")
            if fr.doesnt_untap(ff):
                details.append(f"{prefix}does not untap normally")
            if fr.mana_bonus_triggers(ff):
                details.append(f"{prefix}adds bonus mana when you tap other permanents")
    return source, details, problems


def build_report(store, commanders, names):
    """Report lines for every distinct card. Cards with nothing to report are counted, not listed."""
    lines, quiet, diffs, unscripted, unknown = [], 0, 0, [], []
    for name in sorted(set(names), key=str.lower):
        source, details, problems = describe_card(store, commanders, name)
        if source == "no Scryfall data":
            unknown.append(name)
        elif source == "card text only":
            unscripted.append(name)
        for p in problems:
            diffs += 1
            lines.append(f"DIFFERENT  {name}: {p}")
        if details:
            lines.append(f"{name}  [{source}]")
            lines.extend(f"    {d}" for d in details)
        elif not problems:
            quiet += 1
    head = [f"{len(set(names))} different cards: {quiet} have no land / mana / fetch rules to check.",
            f"{len(unscripted)} card(s) have no Forge script and use the card text: " + (", ".join(unscripted) or "none"),
            f"{len(unknown)} card(s) not found on Scryfall: " + (", ".join(unknown) or "none"),
            f"{diffs} place(s) where the Forge script and the card text DISAGREE (see DIFFERENT below)."
            if diffs else "The Forge scripts and the card text agree everywhere both were understood.", ""]
    return head + lines


def main(argv):
    from card_data import CardDataStore
    from deck_importer import DeckImportError, import_deck, import_from_text

    source = argv[1] if len(argv) > 1 else DEFAULT_DECK
    try:
        if source.lower().startswith(("http://", "https://")):
            commanders, deck = import_deck(source)
        else:
            with open(os.path.expanduser(source), "r", encoding="utf-8-sig") as f:
                commanders, deck = import_from_text(f.read())
    except (DeckImportError, OSError) as e:
        print(f"Could not load the deck: {e}")
        return 1
    names = set(deck) | set(commanders)
    store = CardDataStore()
    print(f"Loading data for {len(names)} cards (Scryfall, then Forge scripts)...")
    store.prefetch_cards(names)
    have, total = store.prefetch_forge(names)
    print(f"Forge scripts available for {have} of {total} cards.")
    if store.forge.last_error:
        print(f"  note: {store.forge.last_error}")
    lines = build_report(store, commanders, names)
    text = "\n".join(lines)
    print()
    print(text)
    try:
        os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\nSaved to {REPORT_FILE}")
    except OSError as e:
        print(f"\nCould not save the report: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
