# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/draft_sim.py - many Commander cube drafts by four AI drafters, measured (Round CD1). Never imported by the game.

    python tools/draft_sim.py                            the default cube (cm), 200 drafts, from the kept copy or Cube Cobra
    python tools/draft_sim.py --cube <link|id|file.json> --drafts 500 --seed 7
    python tools/draft_sim.py --forge                    also leave out cards this PC's Forge can't play, and give the AI decks
                                                         Forge's "AI:RemoveDeck:All" cards only as a last resort (as the game will)

What it reports, for the AI drafters (Karl's answer 7: "draft like people", deck strength second) - the bar each number is held to is in
docs/incoming/cd1/SPEC_CUBE_DRAFT_2026-10-09.md, section 10 (proposed, not yet agreed):
  legal decks            every AI deck passes draft_deck.check (903.13f, 903.5c, 903.3, 702.124) - must be all of them
  short                  decks with basics standing in for missing spells, and how many
  first commander        the card number (1-60) at which a seat first took a card that can be its commander
  picks like people      at each pack's first pick, how often the AI's two cards are among the pack's top 4 by Elo
                         (Elo is made from people's picks, so this is "takes what people take early")
  on colour later        of the AI's non-land picks from card 25 on, how many fit its final deck's colours (people settle)
  deck strength          the mean Elo z of the decks' spells, and how many colours the decks play
"""
import argparse
import json
import os
import statistics as st
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import cube_source as cs  # noqa: E402  (after sys.path.insert)
import draft_ai as da  # noqa: E402
import draft_core as dc  # noqa: E402
import draft_deck as dd  # noqa: E402

TOP_FOR_EARLY = 4
LATE_FROM_CARD = 25


def load_cards(ref, forge=False, refresh=False):
    """(cube name, cards, ai_flagged names, missing names)."""
    if os.path.isfile(ref):
        with open(ref, encoding="utf-8") as f:
            data = json.load(f)
        data = data.get("cube", data) if isinstance(data, dict) and "cube" in data and "cards" not in data else data
        cube = cs.from_cubecobra(data, os.path.splitext(os.path.basename(ref))[0])
    else:
        cube, _note = cs.load(ref, refresh=refresh)
    cards, flagged, missing = list(cube.cards), frozenset(), ()
    if forge:
        chk = cs.check_forge(cards, cs.forge_script_lookup())
        cards, flagged, missing = list(chk.playable), chk.ai_flagged, chk.missing
    return cube.name or cube.id, cards, flagged, missing


def one_draft(cards, z, seed, flagged):
    d = dc.Draft(len(cards), [dc.Seat(f"AI {i + 1}", "ai") for i in range(dc.SEATS)], seed)
    dc.run_ai(d, da.chooser(cards, seed, z))
    decks = [dd.build(cards, d.pools[s], z, avoid=flagged) for s in range(d.n)]
    return d, decks


def measure(cards, drafts=200, seed=1, flagged=frozenset()):
    z = dd.zscores(cards)
    legal = short_decks = short_cards = 0
    first_cmd, early_hits, early_total, late_on, late_total = [], 0, 0, 0, 0
    strength, colours, flagged_in, lands26 = [], {}, 0, 0
    t0 = time.time()
    for k in range(drafts):
        d, decks = one_draft(cards, z, seed + k, flagged)
        for s, deck in enumerate(decks):
            if deck is None:
                continue
            _cmd, main = dd.deck_names(cards, deck)
            main_ints = list(deck.spells + deck.lands) + [n for n, c in deck.basics.items() for _ in range(c)]
            if not dd.check(cards, d.pools[s], list(deck.commanders), main_ints):
                legal += 1
            if deck.short:
                short_decks += 1
                short_cards += deck.short
            if len(deck.lands) + sum(deck.basics.values()) - deck.short > dd.LANDS:      # round CD4a: a 26th land instead
                lands26 += 1
            pool = d.pools[s]
            firsts = [n for n, i in enumerate(pool, 1) if cards[i].commander]
            if firsts:
                first_cmd.append(firsts[0])
            for n, i in enumerate(pool, 1):
                if n >= LATE_FROM_CARD and not cs.is_land(cards[i]):
                    late_total += 1
                    late_on += dd.fits(cards[i], deck.identity)
            strength.append(st.fmean(z[i] for i in deck.spells) if deck.spells else 0.0)
            colours[len(deck.identity)] = colours.get(len(deck.identity), 0) + 1
            flagged_in += sum(1 for i in deck.spells + deck.lands if cards[i].name in flagged)
        for p in d.log:
            if p.turn != 1:
                continue
            top = sorted(p.pack_before, key=lambda i: -cards[i].elo)[:TOP_FOR_EARLY]
            early_total += len(p.cards)
            early_hits += sum(1 for i in p.cards if i in top)
    seats = drafts * dc.SEATS
    return {"drafts": drafts, "seats": seats, "seconds": round(time.time() - t0, 1), "legal": legal, "short_decks": short_decks,
            "short_cards": short_cards, "first_commander_median": st.median(first_cmd) if first_cmd else None,
            "first_commander_max": max(first_cmd) if first_cmd else None, "no_commander": seats - len(first_cmd),
            "early_top": early_hits / early_total if early_total else 0.0,
            "late_on_colour": late_on / late_total if late_total else 0.0,
            "strength_mean": st.fmean(strength) if strength else 0.0, "colours": dict(sorted(colours.items())),
            "flagged_in_decks": flagged_in, "lands26": lands26}


def report(name, cards, m, flagged, missing):
    seats = m["seats"]
    lines = [f"Cube: {name} - {len(cards)} draftable cards, {sum(c.commander for c in cards)} can be commanders",
             f"Drafts: {m['drafts']} x {dc.SEATS} AI seats ({dc.PACKS} packs of {dc.PACK_SIZE}, pick {dc.PICKS}), {m['seconds']} s"]
    if missing:
        lines.append(f"Left out (no Forge script): {len(missing)} - {', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")
    if flagged:
        lines.append(f"Forge's AI:RemoveDeck:All cards in the cube: {len(flagged)}; in the AI decks: {m['flagged_in_decks']}")
    lines += [f"Legal decks:          {m['legal']} of {seats}",
              f"No commander drafted: {m['no_commander']} of {seats}",
              f"Short decks:          {m['short_decks']} of {seats} ({m['short_cards']} basics standing in for spells in all)",
              f"26 lands:             {m['lands26']} of {seats} (round CD4a: a deck a spell short plays the 24-26 range's top)",
              f"First commander:      median card {m['first_commander_median']}, latest card {m['first_commander_max']}",
              f"Picks like people:    {100 * m['early_top']:.0f}% of first picks among the pack's top {TOP_FOR_EARLY} by Elo",
              f"On colour later:      {100 * m['late_on_colour']:.0f}% of non-land picks from card {LATE_FROM_CARD} fit the final deck",
              f"Deck strength:        mean spell Elo z {m['strength_mean']:+.2f}; colours played {m['colours']}"]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--cube", default=cs.DEFAULT_CUBE)
    ap.add_argument("--drafts", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--forge", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", help="also write the numbers to this file")
    a = ap.parse_args(argv)
    try:
        name, cards, flagged, missing = load_cards(a.cube, a.forge, a.refresh)
    except cs.CubeError as e:
        print(f"Can't load the cube: {e}")
        return 2
    m = measure(cards, a.drafts, a.seed, flagged)
    print(report(name, cards, m, flagged, missing))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
    return 0 if m["legal"] == m["seats"] else 1


if __name__ == "__main__":
    sys.exit(main())
