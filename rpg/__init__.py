# SPDX-License-Identifier: GPL-3.0-or-later
"""The RPG mode (Karl's plan of 9 Oct 2026, "Manticore RPG Mode: Plan"): an overworld you walk around, a Shandalar-style
collection, 60-card duels at 20 life.

Phase 2 (Round RPG2) is the data underneath it, with no screen yet:
  cardpool  - every card Forge can play (rpg/data/cards.json, written from Forge's own card database by tools/rpg_build_data.py)
  rules     - the RPG's deck rules: 60 or more, four of a card, a 15-card sideboard, Vintage's restricted list (one copy) and
              banned list (none), read from forge_runtime at run time
  tiers     - a power tier 0-5 per card: from Forge's archetype data (rpg/data/tiers.json), the restricted list, rarity for the
              rest, and Karl's override file (rpg/data/tier_overrides.json) over all of it
  products  - Jumpstart half-decks (the starting deck: pick 3 of 363, read from forge_runtime) and booster packs (opened by
              Forge's own generator at build time, rpg/data/packs/<SET>.json)
  enemies   - enemy decks from Forge's archetype deck builder, checked and soak-tested before they ship (rpg/data/enemies/)

Phase 3 (Rounds RPG4 and RPG5) is the game logic, still with no screen: economy, collection, save, newgame, shop and duel
(RPG4); tmx (a reader for the Tiled editor's map files) and explore (walls, walking, what you walk into, the camera) over the
maps in rpg/maps/ (RPG5). rpg_map_view.py, beside the program, draws a map with pygame.

No pygame anywhere in this package.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")


def data_path(*parts):
    return os.path.join(DATA_DIR, *parts)
