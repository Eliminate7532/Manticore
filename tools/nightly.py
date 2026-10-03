# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\nightly.py - one night of testing in one command (Round 28c, SOAK_PLAN Phase C).

    python tools\\nightly.py --hours 8                  start now, stop 8 hours later
    python tools\\nightly.py --until 07:00              stop at 07:00 (for a scheduled task that starts at 00:30)

In order:
  1. the canary: a short game with a deliberate bridge fault that must be reported (as in tools\\soak.py);
  2. the card-check sweep: card_check.py over the next alpha deck(s), every card put into play in turn with the bridge's
     self-checks on - up to --card-hours (default 2), then it stops and carries on from that card next night.
     Decks go round in this order: the 5 alpha decks, then the Kinnan sample, then (--decks mine) your own decks;
  3. soak games for the rest of the night, half of them from a mid-game board (--boards, default 0.5).

The night's folder is soak_runs\\night_<date>_<time>\\ (card-check reports, the soak's games, nightly_summary.txt).
The latest summary is copied to soak_runs\\nightly_summary.txt, and the sweep's progress is kept in
soak_runs\\nightly_state.json (which decks are done, and where an unfinished one stops).

The night is VALID when the canary was caught, the soak games are VALID (see tools\\soak.py) and no card check broke a
bridge self-check. Card-check FAILs that are not bridge self-checks are listed to look at - card_check.py can't set every
card up (a card that needs a creature to sacrifice, a graveyard to exile from...), so they don't make the night INVALID.
"""
import argparse
import datetime
import json
import os
import random
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "tools"))

ALPHA_DECKS = ["typal_lathril", "go_wide_adeline", "aristocrats_teysa", "voltron_light_paws", "spellslinger_veyran"]
EXTRA_SAMPLES = ["kinnan_nbc_moxfield_export"]
STATE_FILE = "nightly_state.json"
MIN_SOAK_MINUTES = 30          # the card check never takes the whole night: at least this long is left for soak games


# ---------------------------------------------------------------------------------------------
# which deck next
# ---------------------------------------------------------------------------------------------
def deck_rotation(which="sample"):
    """[(stem, path)] in sweep order."""
    out = [(s, os.path.join(BASE_DIR, "sample_decks", s + ".txt")) for s in ALPHA_DECKS + EXTRA_SAMPLES]
    out = [(s, p) for s, p in out if os.path.isfile(p)]
    if which == "mine":
        folder = os.path.join(BASE_DIR, "my_decks")
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith(".txt"):
                    out.append(("mine/" + name[:-4], os.path.join(folder, name)))
    return out


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def next_deck(rotation, state):
    """The deck to check next: one that was started and not finished; else the first never finished; else the one finished
    longest ago. Returns (stem, path, first_card_index)."""
    decks = state.get("decks", {})
    for stem, path in rotation:
        d = decks.get(stem) or {}
        if d.get("next") and not d.get("complete"):
            return stem, path, d["next"]
    for stem, path in rotation:
        if not (decks.get(stem) or {}).get("complete"):
            return stem, path, 0
    oldest = min(rotation, key=lambda sp: (decks.get(sp[0]) or {}).get("complete") or "")
    return oldest[0], oldest[1], 0


def sweep_coverage(rotation, state):
    """One line per alpha deck: when its whole card check last finished, or how far it has got."""
    decks = state.get("decks", {})
    lines = []
    for stem, _path in rotation:
        if stem not in ALPHA_DECKS:
            continue
        d = decks.get(stem) or {}
        if d.get("complete"):
            lines.append("  %-22s complete %s  (%s)" % (stem, d["complete"], d.get("tally", "")))
        elif d.get("next"):
            lines.append("  %-22s in progress: %d of %d cards" % (stem, d["next"], d.get("total", 0)))
        else:
            lines.append("  %-22s not checked yet" % stem)
    done = sum(1 for s in ALPHA_DECKS if (decks.get(s) or {}).get("complete"))
    lines.insert(0, "Alpha decks swept: %d of %d" % (done, len(ALPHA_DECKS)))
    return lines


# ---------------------------------------------------------------------------------------------
# the card check
# ---------------------------------------------------------------------------------------------
def is_bridge_failure(result):
    return result.status == "FAIL" and any(n.startswith("bridge self-check") for n in result.notes)


def run_card_checks(rotation, state, state_path, deadline, out_root, say=print, checker_factory=None):
    """Check cards until `deadline`, deck after deck. Returns [(stem, results, finished)] for this night."""
    import card_check as cc
    night = []
    decks = state.setdefault("decks", {})
    while time.time() < deadline:
        stem, path, start = next_deck(rotation, state)
        if night and any(s == stem for s, _r, _f in night):
            break                                   # every deck done this cycle - don't check one twice in a night
        profiles = cc.load_profiles(path)
        entry = decks.setdefault(stem, {})
        entry["total"] = len(profiles)
        say("card check: %s, from card %d of %d" % (stem, start + 1, len(profiles)))
        checker = (checker_factory or cc.Checker)(path)
        results, i = [], start
        try:
            checker.start()
            while i < len(profiles) and time.time() < deadline:
                rows = checker.check_card(profiles[i])
                results.extend(rows)
                say("  [%d/%d] %-40s %s" % (i + 1, len(profiles), profiles[i].deck_name[:40],
                                            " ".join("%s:%s" % (r.level, r.status) for r in rows)))
                i += 1
                entry["next"] = i
                save_state(state_path, state)       # a night cut short (power cut, Ctrl+C) still resumes from here
        finally:
            checker.stop()
        finished = i >= len(profiles)
        if finished:
            counts = {}
            for r in results:
                counts[r.status] = counts.get(r.status, 0) + 1
            entry.update({"complete": datetime.date.today().isoformat(), "next": 0,
                          "tally": ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items()))})
            save_state(state_path, state)
        report = cc.format_report(results, path) if results else "Card check of %s: nothing checked" % stem
        safe = stem.replace("/", "_")
        with open(os.path.join(out_root, "card_check_%s.txt" % safe), "w", encoding="utf-8") as f:
            f.write(report + "\n")
        with open(os.path.join(out_root, "card_check_%s.json" % safe), "w", encoding="utf-8") as f:
            json.dump([r.as_dict() for r in results], f, indent=1)
        night.append((stem, results, finished))
    return night


# ---------------------------------------------------------------------------------------------
# the night
# ---------------------------------------------------------------------------------------------
def parse_until(text, now=None):
    """'07:00' -> the next 07:00 after now (tomorrow if it's already past)."""
    now = now or datetime.datetime.now()
    hh, mm = (int(x) for x in text.split(":"))
    end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if end <= now:
        end += datetime.timedelta(days=1)
    return end


def night_summary(canary, night, soak_text, coverage, soak_code):
    """nightly_summary.txt: the verdict, the card check, the sweep's progress, then the soak's own summary."""
    bridge_fails = [(stem, r) for stem, results, _f in night for r in results if is_bridge_failure(r)]
    other_fails = [(stem, r) for stem, results, _f in night for r in results if r.status == "FAIL" and not is_bridge_failure(r)]
    reasons = []
    if canary is None or not canary[0]:
        reasons.append("the canary was not caught" if canary else "the canary did not run")
    if not soak_text.startswith("SOAK RUN: VALID"):
        reasons.append("the soak games are not VALID (see below)")
    if bridge_fails:
        reasons.append("%d card check(s) broke a bridge self-check" % len(bridge_fails))
    lines = ["NIGHT: " + ("VALID" if not reasons else "INVALID")]
    lines += ["  - " + r for r in reasons]
    lines.append("Canary: %s" % ("OK" if canary and canary[0] else "FAILED - %s" % (canary[1] if canary else "not run")))
    lines.append("")
    lines.append("CARD CHECK (%d deck(s) tonight)" % len(night))
    for stem, results, finished in night:
        counts = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        lines.append("  %s: %d check(s), %s%s" % (stem, len(results),
                                                  ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items())) or "none",
                                                  "" if finished else "  (not finished - carries on next night)"))
    if bridge_fails:
        lines.append("  BRIDGE SELF-CHECK FAILURES:")
        for stem, r in bridge_fails:
            lines.append("    %s / %s [%s]: %s" % (stem, r.card, r.level, next(n for n in r.notes if n.startswith("bridge self-check"))))
    if other_fails:
        lines.append("  Other card-check FAILs to look at (the checker couldn't finish the card; not counted against the night):")
        for stem, r in other_fails[:40]:
            lines.append("    %s / %s [%s]: %s" % (stem, r.card, r.level, (r.notes or ["?"])[0][:100]))
        if len(other_fails) > 40:
            lines.append("    ... and %d more (see the card_check_*.txt reports)" % (len(other_fails) - 40))
    lines.append("")
    lines += coverage
    lines.append("")
    lines.append("=" * 100)
    lines.append(soak_text.rstrip())
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="One night: the canary, the card-check sweep, then soak games.")
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--hours", type=float, default=None, help="how long the whole night runs (default 8)")
    when.add_argument("--until", default=None, help="stop at this time, HH:MM (e.g. 07:00)")
    ap.add_argument("--card-hours", type=float, default=2.0, dest="card_hours",
                    help="time for the card-check sweep (default 2; 0 skips it)")
    ap.add_argument("--boards", type=float, default=0.5, help="share of soak games from a mid-game board (default 0.5)")
    ap.add_argument("--decks", choices=("sample", "mine"), default="sample",
                    help="mine: after the sample decks, the sweep goes on to your own decks (read-only)")
    ap.add_argument("--out", default=None, help="the folder that holds every night (default: soak_runs next to the program)")
    args = ap.parse_args(argv)

    import soak
    now = datetime.datetime.now()
    end = parse_until(args.until, now) if args.until else now + datetime.timedelta(hours=args.hours or 8.0)
    deadline = end.timestamp()
    history = args.out or soak.default_out_dir()
    out_root = os.path.join(history, "night_" + now.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_root, exist_ok=True)
    os.environ["MANTICORE_DATA_DIR"] = out_root          # before any Forge module is imported (see soak.py)
    soak._load_modules()
    print("night: %s to %s, folder %s" % (now.strftime("%H:%M"), end.strftime("%H:%M"), out_root), flush=True)
    try:                                                   # round 28d fix 1: old nights' game recordings would fill the disk
        import soak_prune
        pruned, freed = soak_prune.prune(history, keep=soak_prune.KEEP_RUNS + 1)     # +1: tonight's folder already exists
        if pruned:
            print("pruned %d old game folder(s), %.1f GB (summaries and bug-report zips kept)" % (pruned, freed / 1e9), flush=True)
    except Exception as e:                                 # never a reason to lose the night
        print("pruning old game folders failed: %s" % e, flush=True)

    canary = soak.run_canary(argparse.Namespace(), out_root)
    print("canary: %s (%s)" % ("OK" if canary[0] else "FAILED", canary[1]), flush=True)

    rotation = deck_rotation(args.decks)
    state_path = os.path.join(history, STATE_FILE)
    state = load_state(state_path)
    night = []
    card_deadline = min(time.time() + args.card_hours * 3600, deadline - MIN_SOAK_MINUTES * 60)
    if args.card_hours > 0 and card_deadline > time.time():
        try:
            night = run_card_checks(rotation, state, state_path, card_deadline, out_root,
                                    say=lambda s: print(s, flush=True))
        except KeyboardInterrupt:
            print("\nstopping (Ctrl+C) during the card check.")

    soak_hours = max(0.0, (deadline - time.time()) / 3600)
    soak_args = argparse.Namespace(games=None, hours=soak_hours, players=None, decks="sample", seed=None, fault=None,
                                   out=os.path.join(out_root, "soak"), history=history, turn_cap=soak.TURN_CAP,
                                   game_timeout=soak.GAME_TIMEOUT, canary=False, boards=args.boards)
    code = 0
    if soak_hours > 0.05:
        code = soak.run(soak_args)
    try:
        with open(os.path.join(out_root, "soak", "soak_summary.txt"), encoding="utf-8") as f:
            soak_text = f.read()
    except OSError:
        soak_text = "SOAK RUN: INVALID - no soak games ran\n"

    text = night_summary(canary, night, soak_text, sweep_coverage(rotation, state), code)
    for folder in (out_root, history):
        with open(os.path.join(folder, "nightly_summary.txt"), "w", encoding="utf-8") as f:
            if folder == history:
                f.write("(the latest night: %s)\n\n" % os.path.basename(out_root))
            f.write(text)
    print("\n" + text.split("=" * 100)[0])
    return 0 if text.startswith("NIGHT: VALID") else 1


if __name__ == "__main__":
    sys.exit(main())
