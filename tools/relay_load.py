# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/relay_load.py - Round RLY1: several online games at the same time through ONE relay.

    python tools/relay_load.py --tables 3 --games 4 [--relay HOST:PORT | --start-relay] [--seed N] [--out DIR]
                               [--pods 0.3] [--drops 0.5] [--concede 0.7] [--resume 0]

Each "table" is its own tools/online_soak.py process (a host engine, its guests, soak bots on every seat) with --relay, so the
games run side by side, each in its own room at the relay - what friends hosting at the same time would do. With --start-relay
the relay is started here on 127.0.0.1 (its log is kept); with --relay it is a relay somewhere else (the Oracle server).

The verdict (relay_load_summary.txt):
  PASS  every table played at least one game, every game joined through the relay, no game has a failing finding, and at the
        busiest moment at least --tables games were running at once (measured from the games' own start and end times;
        with a local relay, also from its log of rooms opened and closed)
  FAIL  anything else, with the reason

Output: <out>/table_N/ (each table's online_summary.txt and online_results.json), relay.log (a local relay),
relay_load_summary.txt.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tools import online_soak  # noqa: E402  (parse_relay, start_local_relay: no Forge needed for those)

PASS_THROUGH = ("pods", "drops", "concede", "leave", "resume", "turn_cap", "game_timeout", "format")
ROOM_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+) room (\S+) (opened|closed)$")


def peak_overlap(intervals):
    """The most intervals (start, end) open at one moment, and that moment. An end at the same time as a start doesn't overlap."""
    events = []
    for s, e in intervals:
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))           # ends before starts at the same moment
    best, best_at, now = 0, None, 0
    for t, d in events:
        now += d
        if now > best:
            best, best_at = now, t
    return best, best_at


def rooms_from_log(lines):
    """[(opened, closed)] per room from a relay log (rooms still open at the end close at the last line's time)."""
    opened, spans, last = {}, [], None
    for line in lines:
        m = ROOM_LINE.match(line.strip())
        if not m:
            continue
        t = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(m.group(2)) / 1000.0
        last = t
        if m.group(4) == "opened":
            opened[m.group(3)] = t
        elif m.group(3) in opened:
            spans.append((opened.pop(m.group(3)), t))
    for t0 in opened.values():
        spans.append((t0, last))
    return spans


def judge(tables, rows_by_table, room_spans=None):
    """(verdict, reasons, facts) from each table's online_results.json rows."""
    reasons, facts = [], {}
    all_rows = [r for rows in rows_by_table for r in rows]
    facts["games"] = len(all_rows)
    facts["game_over"] = len([r for r in all_rows if r.get("ended") == "game_over"])
    facts["via_relay"] = len([r for r in all_rows if r.get("via") == "relay"])
    facts["failing_games"] = [(t, r["index"], r.get("fails")) for t, rows in enumerate(rows_by_table) for r in rows if r.get("fails")]
    for t, rows in enumerate(rows_by_table):
        if not rows:
            reasons.append("table %d played no game" % t)
    if facts["via_relay"] != facts["games"]:
        reasons.append("%d of %d games did not join through the relay" % (facts["games"] - facts["via_relay"], facts["games"]))
    if facts["failing_games"]:
        reasons.append("%d game(s) with a failing finding: %s" % (len(facts["failing_games"]), ", ".join(
            "table %d game %d (%s)" % (t, i, ",".join(f)) for t, i, f in facts["failing_games"][:6])))
    spans = [(r["started"], r["started"] + r["seconds"]) for r in all_rows if r.get("started")]
    facts["peak_games"], at = peak_overlap(spans)
    facts["peak_games_at"] = datetime.datetime.fromtimestamp(at).strftime("%H:%M:%S") if at else None
    if facts["peak_games"] < tables:
        reasons.append("at most %d game(s) ran at the same time (wanted %d)" % (facts["peak_games"], tables))
    if room_spans is not None:
        facts["rooms_opened"] = len(room_spans)
        facts["peak_rooms"], _at = peak_overlap(room_spans)
        if facts["peak_rooms"] < tables:
            reasons.append("the relay's log shows at most %d room(s) open at once (wanted %d)" % (facts["peak_rooms"], tables))
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def summary_text(verdict, reasons, facts, args, relay_addr, started, table_summaries):
    lines = ["RELAY LOAD TEST: %s" % verdict,
             "relay: %s:%s%s" % (relay_addr[0], relay_addr[1], " (started by this test)" if args.start_relay else ""),
             "tables at once: %d, games per table: %s, started %s, finished %s" % (
                 args.tables, args.games if args.games is not None else "for %.2f h" % args.hours, started,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
             "games: %d (game over %d), joined through the relay: %d" % (facts["games"], facts["game_over"], facts["via_relay"]),
             "most games running at once: %d (at %s)" % (facts["peak_games"], facts["peak_games_at"])]
    if "peak_rooms" in facts:
        lines.append("relay log: %d room(s) opened, at most %d open at once" % (facts["rooms_opened"], facts["peak_rooms"]))
    lines.append("")
    lines += ["WHY NOT A PASS:"] + ["  - " + r for r in reasons] + [""] if reasons else []
    for t, path in enumerate(table_summaries):
        lines.append("---- table %d (%s)" % (t, path))
        try:
            with open(path, encoding="utf-8") as f:
                lines += ["  " + ln for ln in f.read().splitlines()[:14]]
        except OSError:
            lines.append("  (no summary: the table never finished a game)")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Several online games at once through one relay (round RLY1).")
    ap.add_argument("--tables", type=int, default=3)
    ap.add_argument("--games", type=int, default=None, help="games per table (default 3 when --hours isn't given)")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--relay", default=None, metavar="HOST:PORT")
    ap.add_argument("--start-relay", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    for name, default in (("pods", 0.3), ("drops", 0.5), ("concede", 0.7), ("leave", 0.2), ("resume", 0.0)):
        ap.add_argument("--" + name, type=float, default=default)
    ap.add_argument("--turn-cap", type=int, default=None)
    ap.add_argument("--game-timeout", type=int, default=None)
    ap.add_argument("--format", default=None)
    args = ap.parse_args(argv)
    if bool(args.relay) == bool(args.start_relay):
        ap.error("give --relay HOST:PORT or --start-relay")
    if args.games is None and args.hours is None:
        args.games = 3
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = args.out or os.path.join(BASE_DIR, "soak_runs", "relay_load_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    relay_proc, log_path = None, None
    if args.start_relay:
        log_path = os.path.join(out, "relay.log")
        relay_proc, relay_addr = online_soak.start_local_relay(log_path)
    else:
        relay_addr = online_soak.parse_relay(args.relay)
    seed = args.seed if args.seed is not None else int(time.time()) % 100000
    procs, outs = [], []
    try:
        for t in range(args.tables):
            t_out = os.path.join(out, "table_%d" % t)
            cmd = [sys.executable, os.path.join(BASE_DIR, "tools", "online_soak.py"), "--relay", "%s:%s" % relay_addr,
                   "--seed", str(seed + 1000 * t), "--out", t_out]
            cmd += ["--games", str(args.games)] if args.games is not None else ["--hours", str(args.hours)]
            for name in PASS_THROUGH:
                v = getattr(args, name, None)
                if v is not None:
                    cmd += ["--" + name.replace("_", "-"), str(v)]
            log = open(os.path.join(out, "table_%d.out" % t), "w", encoding="utf-8")
            procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=BASE_DIR), log))
            outs.append(t_out)
            print("table %d started" % t, flush=True)
        for p, log in procs:
            p.wait()
            log.close()
    finally:
        for p, log in procs:
            if p.poll() is None:
                p.terminate()
        if relay_proc is not None:
            relay_proc.terminate()
            try:
                relay_proc.wait(10)
            except Exception:
                relay_proc.kill()
    rows_by_table = []
    for t_out in outs:
        try:
            with open(os.path.join(t_out, "online_results.json"), encoding="utf-8") as f:
                rows_by_table.append(json.load(f))
        except (OSError, ValueError):
            rows_by_table.append([])
    room_spans = None
    if log_path:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            room_spans = rooms_from_log(f.read().splitlines())
    verdict, reasons, facts = judge(args.tables, rows_by_table, room_spans)
    text = summary_text(verdict, reasons, facts, args, relay_addr, started,
                        [os.path.join(t_out, "online_summary.txt") for t_out in outs])
    with open(os.path.join(out, "relay_load_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
