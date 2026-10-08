# SPDX-License-Identifier: GPL-3.0-or-later
"""
soak_report.py - Round 28ba: is a soak run worth believing, and what did it find?

The first real overnight soak (27 Sept 2026) said "0 game(s) with a failing finding" while the bot in my seat never
played a single card (0 permanents all game, every game) and only one kind of question was ever asked. Nothing in the
summary said so. This module makes a run prove it tested something before its "0 failures" counts:

  game_health(messages)      what the bot actually did in one game, from its record file: lands played, spells cast,
                             questions answered, kinds of question seen, clicks Forge dropped, seats at the table
  run_verdict(healths, ...)  VALID or INVALID, with the reasons, for the whole run
  signature(finding)         a finding with its ids and numbers taken out, so the same bug in 12 games is one line
  summary_text(...)          the whole soak_summary.txt

Pure Python: no pygame, no Forge. tools/soak.py calls it; tests/test_round28ba.py tests it on the first night's
recordings (tests/fixtures/soak/).
"""
import json
import re

# A run is INVALID (its "0 failures" means nothing) when, over all the games that got past the start:
MIN_LANDS_PER_GAME = 2          # fewer lands than this per game, on average, by the bot's seat
MIN_SPELLS = 1                  # no spell cast by the bot's seat in the whole run
MIN_QUESTION_KINDS = 3          # fewer distinct kinds of question (Round 27d "shapes") in the whole run


def game_health(messages):
    """What the bot's seat did in one game. messages: a record file's messages (bridge_rules.load_stream)."""
    me = None
    seats = 0
    lands = spells = answered = dropped = 0
    kinds = set()
    memory = {}
    stops, passed = set(), 0                                  # patch 43: priority questions the seat was asked; passed for it
    for m in messages:
        t = m.get("t")
        if t == "passed":
            passed += 1
        elif t == "game_over":                                  # round 28d: the bridge's memory figures for this game
            memory = {k: m[k] for k in ("peakHeapMb", "peakLiveMb", "maxHeapMb") if isinstance(m.get(k), (int, float))}
        elif t == "state":
            if me is None and m.get("me") is not None:
                me = m.get("me")
            seats = max(seats, len(m.get("players") or []))
            if m.get("asking") and m.get("input") == "InputPassPriority" and m.get("inputSeq") is not None:
                stops.add(m.get("inputSeq"))
        elif t == "event":
            if me is None or m.get("player") != me:
                continue
            if m.get("kind") == "land":
                lands += 1
            elif m.get("kind") == "cast" and not m.get("trigger") and not m.get("ability"):
                spells += 1
        elif t == "_sent":
            if (m.get("cmd") or {}).get("c") == "reply":
                answered += 1
        elif t == "shape":
            kinds.add("%s|%s" % (m.get("q"), m.get("shape")))
        elif t == "dropped":
            dropped += 1
    return {"seats": seats, "lands": lands, "spells": spells, "answered": answered,
            "kinds": sorted(kinds), "dropped": dropped, "memory": memory, "stops": len(stops), "passed": passed}


def stop_lines(healths):
    """Patch 43: how many priority questions the soak seat was asked a game (each one an OK for a person), per number of
    players, and how many opponents' triggers / abilities the bridge passed for it. Night 12, before patch 43: 175 a game
    (110 two-player, 182 three, 247 four)."""
    by_seats = {}
    for h in healths:
        if not h.get("seats") or "stops" not in h:
            continue
        cur = by_seats.setdefault(h["seats"], [0, 0, 0])
        cur[0] += 1
        cur[1] += h.get("stops", 0)
        cur[2] += h.get("passed", 0)
    if not by_seats:
        return []
    games = sum(c[0] for c in by_seats.values())
    out = ["PRIORITY STOPS (questions the soak seat was asked, a game)",
           "  all: %.0f a game over %d game(s); %.1f opponents' triggers/abilities passed for it"
           % (sum(c[1] for c in by_seats.values()) / games, games, sum(c[2] for c in by_seats.values()) / games)]
    for seats in sorted(by_seats):
        n, st, ps = by_seats[seats]
        out.append("  %d players: %.0f a game (%d game(s)), %.1f passed" % (seats, st / n, n, ps / n))
    return out


def memory_lines(healths):
    """Round 28d: the most memory any game needed, per number of players - to set Java's heap limit and the minimum PC from
    real games (ALPHA_FINISH_LINE must-have 14). "live" is what was still in use just after a garbage collection (what the
    game really needs); "heap" includes garbage not yet collected (an upper bound)."""
    by_seats = {}
    for h in healths:
        mem = h.get("memory") or {}
        if "peakLiveMb" not in mem and "peakHeapMb" not in mem:
            continue
        cur = by_seats.setdefault(h.get("seats") or 0, {"live": 0, "heap": 0, "max": 0, "games": 0})
        cur["live"] = max(cur["live"], mem.get("peakLiveMb", 0))
        cur["heap"] = max(cur["heap"], mem.get("peakHeapMb", 0))
        cur["max"] = max(cur["max"], mem.get("maxHeapMb", 0))
        cur["games"] += 1
    if not by_seats:
        return ["MEMORY", "  not reported (a bridge from before round 28d, or no game reached game over)"]
    out = ["MEMORY (largest per game, from games that reached game over)"]
    for seats in sorted(by_seats):
        c = by_seats[seats]
        out.append("  %d players: live %d MB, heap %d MB (Java limit %d MB; %d game(s))"
                   % (seats, c["live"], c["heap"], c["max"], c["games"]))
    return out


def run_verdict(healths, canary=None):
    """(valid, reasons). healths: one game_health() per game that got going (a game that crashed before any state has
    seats == 0 and is left out). canary: None (no canary this run), or (ok, detail)."""
    reasons = []
    if canary is not None and not canary[0]:
        reasons.append("the canary game did not report its deliberate fault (%s): the checks themselves may be broken" % canary[1])
    played = [h for h in healths if h.get("seats")]
    if not played:
        reasons.append("no game got past the start")
        return False, reasons
    # Round 28c: land drops are only counted in games that started at turn 1. A game from a mid-game board starts with
    # 5-6 lands in play and often none in hand, so few land drops there says nothing about the bot.
    from_turn1 = [h for h in played if not h.get("board")]
    lands = sum(h["lands"] for h in from_turn1)
    spells = sum(h["spells"] for h in played)
    kinds = set()
    for h in played:
        kinds.update(h["kinds"])
    if from_turn1 and lands < MIN_LANDS_PER_GAME * len(from_turn1):
        reasons.append("the bot played %d land(s) in %d game(s) from turn 1 (at least %d expected)" % (
            lands, len(from_turn1), MIN_LANDS_PER_GAME * len(from_turn1)))
    if spells < MIN_SPELLS:
        reasons.append("the bot cast no spells")
    if len(kinds) < MIN_QUESTION_KINDS:
        reasons.append("only %d kind(s) of question came up (at least %d expected)" % (len(kinds), MIN_QUESTION_KINDS))
    return (not reasons), reasons


_IDS = re.compile(r"\(\d+\)|\b\d+\b")


def signature(finding):
    """The same problem in different games gets the same signature: rule + detail, with card ids and numbers removed."""
    detail = _IDS.sub("#", str(finding.get("detail") or ""))
    detail = " ".join(detail.split())[:160]
    return "%s: %s" % (finding.get("rule"), detail)


def group_findings(results):
    """{signature: {"severity", "games": [index, ...], "zips": [...]}} over all games' findings."""
    groups = {}
    for r in results:
        for f in r.findings:
            g = groups.setdefault(signature(f), {"severity": f.get("severity"), "games": [], "zips": []})
            if f.get("severity") == "fail":
                g["severity"] = "fail"
            if r.index not in g["games"]:
                g["games"].append(r.index)
            if r.zip_path and r.zip_path not in g["zips"]:
                g["zips"].append(r.zip_path)
    return groups


def load_known(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def save_known(path, known):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(known), f, indent=1)
    except OSError:
        pass


def summary_text(header, results, healths, verdict, canary, groups, new_signatures):
    """soak_summary.txt. header: list of "key: value" lines (program version, options, times)."""
    valid, reasons = verdict
    lines = ["SOAK RUN: %s" % ("VALID" if valid else "INVALID - its results do not show the game is fine")]
    for r in reasons:
        lines.append("  - " + r)
    if canary is None:
        lines.append("Canary: not run")
    else:
        lines.append("Canary: %s" % ("OK (the deliberate fault was caught)" if canary[0] else "FAILED - " + canary[1]))
    lines.append("")
    lines += header
    lines.append("")
    fails = {s: g for s, g in groups.items() if g["severity"] == "fail"}
    warns = {s: g for s, g in groups.items() if g["severity"] != "fail"}
    lines.append("PROBLEMS (%d kind(s); %d new since the last run)" % (len(fails), len([s for s in fails if s in new_signatures])))
    for s, g in sorted(fails.items(), key=lambda kv: (kv[0] not in new_signatures, kv[0])):
        lines.append("  %s%s  - games %s" % ("NEW  " if s in new_signatures else "", s, ", ".join(str(i) for i in g["games"])))
        for z in g["zips"][:3]:
            lines.append("        " + z)
    if not fails:
        lines.append("  none")
    lines.append("")
    lines.append("WARNINGS (%d kind(s))" % len(warns))
    for s, g in sorted(warns.items()):
        lines.append("  %s  - games %s" % (s, ", ".join(str(i) for i in g["games"])))
    if not warns:
        lines.append("  none")
    lines.append("")
    lines += memory_lines(healths)
    lines.append("")
    stops = stop_lines(healths)                         # patch 43
    if stops:
        lines += stops
        lines.append("")
    lines.append("GAMES")
    for r, h in zip(results, healths):
        lines.append("  game %d: seed=%s seats=%d turns=%d ended=%s | bot: %d land(s), %d spell(s), %d answer(s), "
                     "%d kind(s) of question, %d dropped click(s)%s"
                     % (r.index, r.seed, h.get("seats") or r.players, r.turns, r.ended, h["lands"], h["spells"],
                        h["answered"], len(h["kinds"]), h["dropped"],
                        ("  -> " + ", ".join(r.fail_rules())) if r.fail_rules() else "")
                     + ("  [started from a mid-game board]" if getattr(r, "start", "turn 1") == "board" else ""))
    lines.append("")
    lines.append("%d game(s) with a failing finding." % sum(1 for r in results if r.fail_rules()))
    return "\n".join(lines) + "\n"
