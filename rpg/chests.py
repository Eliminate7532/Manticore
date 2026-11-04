# SPDX-License-Identifier: GPL-3.0-or-later
"""Treasure chests on the Adventure's maps (Round PROG2; Karl, 2 Nov 2026: "Mostly one-time: hand-placed chests open once; a few
small ones refill each new in-game day"; the design is the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.2).
No pygame.

A chest is a map object of class chest with an id (tools/rpg_make_maps.py's "chest" marker); its contents are
rpg/data/progression.json's chests.list entry of that id (rpg.progress checks it). Walking into it (or a tap on it: rpg.tapwalk)
opens it once. save.world["chests"] = {chest id: game minutes when it was opened} (a save from before has none: every chest is
closed). A daily chest is closed again from the first refill_hour (07:00) after it was opened - the next morning, or the same
morning when it was opened before 07:00.

    is_open(world, cid, entry, refill_hour, now)    open now?
    open_chest(save, cid, entry, ...)               the contents into the save, the chest marked open; returns the lines to say
    CONTENTS                                        what each kind of content says

Readings of mine (Karl's to change): "a card from the band in the player's colours" is a card of the region's tier band that the
player's deck can cast (rpg.rewards.castable), the best tier first when the band has it (like a quest's rare from the band);
"gear" gives its number as gold until gear exists (Round PROG4) and says so; a pack is one of the region's packs opened at once.
"""
from rpg import clock

TITLE = "Treasure"
EMPTY_LINE = "The chest is empty."
GEAR_LATER = "(Gear comes later; for now, its worth in gold.)"


def opened_at(world, cid):
    v = (world.get("chests") or {}).get(cid) if isinstance(world.get("chests"), dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def refill_after(minutes, refill_hour):
    """The first refill_hour strictly after `minutes` (game minutes since the game began)."""
    day_start = (minutes // clock.DAY) * clock.DAY
    at = day_start + refill_hour * 60
    return at if at > minutes else at + clock.DAY


def is_open(world, cid, entry, refill_hour, now):
    t = opened_at(world, cid)
    if t is None:
        return False
    if entry and entry.get("daily"):
        return now < refill_after(t, refill_hour)
    return True


def _band_card(save, pool, tier_table, lists, numbers, band, colours, rng):
    from rpg import rewards
    from rpg.shop import candidates
    for t in range(band[1], band[0] - 1, -1):
        names = [n for n in candidates(t, "", pool, tier_table, lists, numbers.shop_skip_prefixes)
                 if rewards.castable(pool.get(n), colours)]
        if names:
            return rng.choice(names)
    return None


def open_chest(save, cid, entry, pool, tier_table, lists, numbers, band, now=None, refill_hour=None):
    """Open chest `cid`: its contents into the save, the chest marked open. Returns (lines, cards) - the lines that say what was
    in it, and [(name, new)] of the cards it gave. Nothing happens (([], [])) for a chest that is open now."""
    from rpg import products, progress, rewards
    w = save.world
    now = clock.now(w) if now is None else now
    if refill_hour is None:
        refill_hour = progress.read().refill_hour
    if entry is None or is_open(w, cid, entry, refill_hour, now):
        return [], []
    lines, cards = [], []
    gold = int(entry.get("gold") or 0) + int(entry.get("gear") or 0)
    if gold:
        save.gold += gold
        lines.append(f"+{gold} gold.")
        if entry.get("gear"):
            lines.append(GEAR_LATER)
    rng = save.rng("chest:" + cid)
    if entry.get("card") == "band":
        pick = _band_card(save, pool, tier_table, lists, numbers, band, rewards.deck_colours(save.deck(), pool), rng)
        if pick:
            cards.append((pick, progress.is_new(save, pick)))
            save.collection.add(pick, 1)
            lines.append(f"New card: {pick}.")
    code = entry.get("pack")
    if code and products.has_packs(code):
        got = []
        for name, _rarity, _foil in products.open_pack(code, save.rng("pack:" + code)):
            card = pool.get(name)
            shown = card.name if card is not None else name
            cards.append((shown, progress.is_new(save, shown)))
            save.collection.add(shown, 1)
            got.append(shown)
        lines.append(f"A {products.read_pool(code).get('name', code)} pack: " + ", ".join(got) + ".")
    if not lines:
        lines.append(EMPTY_LINE)
    w.setdefault("chests", {})
    if not isinstance(w["chests"], dict):
        w["chests"] = {}
    w["chests"][cid] = round(float(now), 3)
    return lines, cards
