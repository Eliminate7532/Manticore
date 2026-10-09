# SPDX-License-Identifier: GPL-3.0-or-later
"""
replay.py - play a bug report's game again.

    python replay.py bugreport_20260921_183000_Karl.zip              replay every click, then compare with the report's picture of the board
    python replay.py report.zip --upto 40                            stop after the first 40 clicks (to look at the board just before something went wrong)
    python replay.py report.zip --log                                also print the game log the replay produced
    python replay.py report.zip --json after.json                    save the board the replay ended on (state.json format)
    python replay.py report.zip --save-as NAME --note "what it guards"   keep this game as a regression test (tests/reports/NAME/)
    python replay.py --check-saved [NAME]                            replay the saved regression games and say which no longer play out the same

A report holds the two things that make a game repeatable: the shuffle seed (Forge's shuffles AND its AI draw from it) and every click
the player made (commands.json), plus the deck files. Replaying starts the same game and sends the same clicks in the same order, each one
when the engine has settled, which does not depend on how fast the player was. It needs the Forge runtime (Java), like the game does.
Reports written before round 12 have no seed ("Seed: None") and only the newest 300 clicks: they cannot be replayed, and this says so.
No pygame in here.
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile

import forge_client as fc

FOLDER = os.path.dirname(os.path.abspath(__file__))
REGRESSION_DIR = os.path.join(FOLDER, "tests", "reports")      # one folder per saved game: report.zip + expected.json
LOG_TAIL = 40                                                   # how many of the last game-log lines a saved game must reproduce

SETTLE = 0.35          # seconds without a new snapshot before the next click is sent
START_LIMIT = 120.0     # how long to wait for the engine's first board (start-up on a busy PC can take longer than WAIT_LIMIT; round-22 fix)
WAIT_LIMIT = 25.0      # how long to wait for the engine (a button to enable, a question to appear) before saying the replay has diverged
FAST_SETTLE = 0.05     # round UNDO1: the quiet before the FIRST click on a question when the journal says which clicks were dropped
BUSY_RETRIES = 3       # round UNDO1: a click the bridge drops as "busy" (a mana ability still running) is sent again this often
BUSY_WINDOW = 0.12     # round UNDO1: how long to watch for that "dropped" answer after a click (it comes within milliseconds)


class ReportError(Exception):
    """The report cannot be replayed (the message says why, in plain words)."""


class Report:
    def __init__(self, seed, commands, decks, complete, state, name, log_lines):
        self.seed, self.commands, self.decks, self.complete = seed, commands, decks, complete
        self.state, self.name, self.log_lines = state, name, log_lines


def load_report(path):
    """Read a bug report zip. Raises ReportError when it has no seed, no clicks or no decks."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ReportError(f"cannot open {path}: {e}")
    names = set(z.namelist())
    if "commands.json" not in names:
        raise ReportError("this report has no commands.json (it was trimmed to fit, or it is from an old version)")
    data = json.loads(z.read("commands.json").decode("utf-8"))
    seed = data.get("seed")
    if seed is None:
        raise ReportError("this report says 'Seed: None': that game was shuffled at random and cannot be repeated. Reports from round 12 on carry a seed.")
    commands = [{k: v for k, v in c.items() if k != "t"} for c in data.get("commands", [])]
    complete = bool(data.get("complete", False))
    decks = {n.split("/", 1)[1]: z.read(n).decode("utf-8") for n in names if n.startswith("decks/") and n.lower().endswith(".dck")}
    if "player.dck" not in decks or not any(re.fullmatch(r"opponent\d+\.dck", n) for n in decks):
        raise ReportError("this report has no player.dck / opponentN.dck deck files")
    state = json.loads(z.read("state.json").decode("utf-8")) if "state.json" in names else None
    name = "Karl"
    if state:
        me = next((p for p in state.get("players", []) if p.get("id") == state.get("me")), None)
        if me and me.get("name"):
            name = me["name"]
    log_lines = z.read("game_log.txt").decode("utf-8").splitlines() if "game_log.txt" in names else []
    return Report(seed, commands, decks, complete, state, name, log_lines)


def summary(state, any_waiting=False):
    """The parts of a board worth comparing: turn, phase, prompt, and for each player their life and the card names in each zone.
    any_waiting (round MP2b): a "Waiting for <someone>..." prompt counts as just "waiting" - a table that isn't asked anything
    keeps the last name Forge put there, which can be stale (the online soak's first resumed game)."""
    if not state:
        return {}
    prompt = (state.get("prompt", {}).get("message") or "").split("\n")[0]
    if any_waiting and prompt.startswith("Waiting for"):
        prompt = "Waiting for ..."
    out = {"turn": state.get("turn"), "phase": state.get("phase"), "prompt": prompt, "players": {}}
    for p in state.get("players", []):
        zones = {z: sorted(c.get("name", "?") for c in cards) for z, cards in p.get("zones", {}).items() if z in ("battlefield", "graveyard", "exile", "command", "hand")}
        out["players"][p.get("name")] = {"life": p.get("life"), "zones": zones}
    return out


def differences(want, got):
    """Human-readable differences between two summaries ([] when the boards match)."""
    diffs = []
    for k in ("turn", "phase", "prompt"):
        if want.get(k) != got.get(k):
            diffs.append(f"{k}: report {want.get(k)!r}, replay {got.get(k)!r}")
    for name, w in want.get("players", {}).items():
        g = got.get("players", {}).get(name)
        if g is None:
            diffs.append(f"{name}: missing in the replay")
            continue
        if w["life"] != g["life"]:
            diffs.append(f"{name}: life {w['life']} in the report, {g['life']} in the replay")
        for z, cards in w["zones"].items():
            if cards != g["zones"].get(z):
                diffs.append(f"{name}: {z} differs (report {len(cards)} cards, replay {len(g['zones'].get(z, []))})")
    return diffs


class Replayer:
    """Drives one session through a report's clicks. `session` is a started ForgeSession (or a stand-in with the same methods)."""

    def __init__(self, session, commands, settle=SETTLE, wait_limit=WAIT_LIMIT, progress=None, start_limit=START_LIMIT,
                 drops=None, fast_from=None, cancel=None):
        """Round UNDO1 (all three optional; without them the replay is exactly what it was):
        drops     - indexes of commands the bridge dropped in the original game (journal "drop" lines): left out.
        fast_from - from this command on the journal recorded every drop, so a click is sent as soon as its question is asked
                    (FAST_SETTLE) instead of after SETTLE of quiet: every click left is one the original game applied.
        cancel    - a function; when it returns True the replay stops (KeyboardInterrupt), even while it waits."""
        self.s, self.commands, self.settle, self.wait_limit, self.progress = session, list(commands), settle, wait_limit, progress
        self.start_limit = start_limit
        self.drops = set(drops or ())
        self.fast_from = fast_from
        self.cancel = cancel
        self.sent = 0
        self.skipped = 0               # round UNDO1: dropped clicks left out
        self.resent = 0                # round UNDO1: clicks sent again after a "busy" drop
        self.diverged = None           # (index, reason) once the replay could not follow the report

    def pump(self, seconds=0.03):
        end = time.time() + seconds
        while time.time() < end:
            if self.cancel is not None and self.cancel():
                raise KeyboardInterrupt("cancelled")
            self.s.poll()
            time.sleep(0.01)

    def settled(self, settle=None):
        """Wait until the engine has produced no new snapshot for `settle` seconds (or the wait limit passes)."""
        settle = self.settle if settle is None else settle
        last, seen, t0 = time.time(), self.s.state_version, time.time()
        while time.time() - t0 < self.wait_limit:
            self.pump()
            if self.s.exited or self.s.fatal:
                return False
            if self.s.state_version != seen:
                seen, last = self.s.state_version, time.time()
            elif self.s.state is not None and time.time() - last >= settle:
                return True
        return False

    def question(self):
        """(inputSeq, asking) of the newest snapshot. A bridge from before round 28bb has no "asking": then it counts as asking."""
        st = self.s.state or {}
        asking = st.get("asking")
        return (st.get("inputSeq") or 0), (True if asking is None else bool(asking))

    def at_question(self, at):
        """Round UNDO1: True once Forge asks question `at` (or has gone past it)."""
        seq, asking = self.question()
        return seq > at or (seq == at and asking)

    def _send_numbered_fast(self, i, cmd, prev_at):
        """Round UNDO1: send a click the original game applied, as soon as its question is asked. Returns None, or why it can't."""
        at, c = cmd["at"], cmd.get("c")
        if not self.wait_for(lambda: self.at_question(at)):
            return f"the engine never reached question {at} (click {i + 1}: {cmd})"
        if self.question()[0] > at:
            return f"the engine went past question {at} before click {i + 1} ({cmd}), which the original game applied"
        self.settled(self.settle if at == prev_at else FAST_SETTLE)     # a second click on one question: the old careful wait
        if c in ("ok", "cancel") and not self.wait_for(lambda: self.button_ready(c) or self.question()[0] != at):
            return f"the {c} button never became available (click {i + 1}: {cmd})"
        for attempt in range(BUSY_RETRIES + 1):
            if self.question()[0] != at:
                return f"the engine went past question {at} before click {i + 1} ({cmd}), which the original game applied"
            seen = len(getattr(self.s, "dropped", []) or [])
            self.s.send(**cmd)
            busy = self._dropped_busy(c, at, seen)
            if not busy:
                return None
            if attempt < BUSY_RETRIES:
                self.resent += 1
                self.settled()
        return f"the engine stayed busy at question {at} (click {i + 1}: {cmd})"

    def _dropped_busy(self, c, at, seen, window=BUSY_WINDOW):
        """Round UNDO1: after a click, did the bridge drop it because a mana ability was still running? The bridge answers a
        dropped click at once ({"t": "dropped"}), so this waits only until the click shows (the question moves on) or a short
        window passes."""
        end = time.time() + window
        while time.time() < end:
            self.pump(0.01)
            for m in list(getattr(self.s, "dropped", []) or [])[seen:]:
                if m.get("c") == c and m.get("at") == at:
                    return m.get("reason") == "busy"
            if self.question()[0] != at:
                return False
        return False

    def wait_for(self, cond, limit=None):
        t0 = time.time()
        while time.time() - t0 < (self.wait_limit if limit is None else limit):
            self.pump()
            if cond():
                return True
            if self.s.exited or self.s.fatal:
                return False
        return False

    def button_ready(self, which):
        prompt = (self.s.state or {}).get("prompt") or {}
        return bool((prompt.get(which) or {}).get("enabled"))

    def run(self, upto=None):
        """Send the clicks. Returns True when every one was sent, False when the replay diverged (self.diverged says where)."""
        if not self.wait_for(lambda: self.s.ready and self.s.state is not None, self.start_limit):
            self.diverged = (0, "the engine never started the game")
            return False
        prev_at = None
        for i, cmd in enumerate(self.commands):
            if upto is not None and i >= upto:
                break
            c = cmd.get("c")
            if c == "quit":                                             # the player closed the game: nothing more to repeat
                break
            fast = self.fast_from is not None and i >= self.fast_from
            if fast and i in self.drops:                                # round UNDO1: the original game never applied it
                self.skipped += 1
                self.sent += 1
                if self.progress:
                    self.progress(i + 1, len(self.commands))
                continue
            if fast and c != "reply" and cmd.get("at") is not None:
                why = self._send_numbered_fast(i, cmd, prev_at)
                prev_at = cmd["at"]
                if why:
                    self.diverged = (i, why)
                    return False
            elif c == "reply":                                            # an answer to a question the engine asks: wait for that question
                req = None

                def asked():
                    nonlocal req
                    req = next((r for r in self.s.requests if r.get("id") == cmd.get("id")), None)
                    return req is not None
                if not self.wait_for(asked):
                    self.diverged = (i, f"the engine never asked question {cmd.get('id')} (click {i + 1}: {cmd})")
                    return False
                self.s.answer(req, cmd.get("value"))
            elif cmd.get("at") is not None:
                # Bridge protocol 2: the click says which question it answered. Wait for the engine to reach that question, then send it
                # unchanged; the bridge applies it or drops it exactly as it did in the original game (round 22).
                at = cmd["at"]
                if not self.wait_for(lambda: ((self.s.state or {}).get("inputSeq") or 0) >= at):
                    self.diverged = (i, f"the engine never reached question {at} (click {i + 1}: {cmd})")
                    return False
                self.settled()
                self.s.send(**cmd)
            else:
                self.settled()
                if c in ("ok", "cancel") and not self.wait_for(lambda: self.button_ready(c)):
                    self.diverged = (i, f"the {c} button never became available (click {i + 1}: {cmd})")
                    return False
                self.s.send(**cmd)
            self.sent += 1
            if self.progress:
                self.progress(i + 1, len(self.commands))
            if self.s.exited or self.s.fatal:
                self.diverged = (i, "the engine stopped: " + str(self.s.fatal or "exited"))
                return False
        self.settled()
        return True

    def reach(self, seq=None, req=None):
        """Round UNDO1: after the clicks, wait for the moment a rewind goes back to - the question `seq` (Forge's question number)
        or the request `req` - and stop there. Returns True when the engine is asking exactly that."""
        if req is not None:
            ok = self.wait_for(lambda: any(r.get("id") == req for r in self.s.requests))
            if not ok:
                self.diverged = (len(self.commands), f"the engine never asked question {req}")
                return False
        else:
            if not self.wait_for(lambda: self.at_question(seq)):
                self.diverged = (len(self.commands), f"the engine never reached question {seq}")
                return False
            if self.question()[0] != seq:
                self.diverged = (len(self.commands), f"the engine went past question {seq} by itself")
                return False
        self.settled()
        return True


def replay(report, runtime=None, upto=None, progress=None, keep_dir=None):
    """Replay a Report. Returns {"state", "log", "diverged", "sent", "differences"}."""
    tmp = keep_dir or tempfile.mkdtemp(prefix="replay_")
    os.makedirs(tmp, exist_ok=True)
    paths = {}
    for name, text in report.decks.items():
        paths[name] = os.path.join(tmp, name)
        with open(paths[name], "w", encoding="utf-8") as f:
            f.write(text)
    opponents = [paths[n] for n in sorted((n for n in paths if re.fullmatch(r"opponent\d+\.dck", n)), key=lambda n: int(re.findall(r"\d+", n)[0]))]
    dev = any(isinstance(c, dict) and c.get("c") == "setup" for c in report.commands)      # a test game that set up its own board
    session = fc.ForgeSession(paths["player.dck"], opponents, name=report.name, seed=report.seed, runtime=runtime, dev=dev)
    session.stderr_path = os.path.join(tmp, "forge_engine.log")
    session.start()
    try:
        r = Replayer(session, report.commands, progress=progress)
        ok = r.run(upto)
        state = session.state
        log = [f"[{e.get('type')}] {e.get('text')}" for e in session.log]
    finally:
        session.close()
    result = {"state": state, "log": log, "diverged": r.diverged, "sent": r.sent, "differences": []}
    if report.state and upto is None and ok:
        result["differences"] = differences(summary(report.state), summary(state))
    return result


# ---- keeping a replayed report as a regression test ----------------------------------------------------------------

def expectation(state, log):
    """What a saved game must keep producing: the final board (turn, phase, life, card names per zone) and the last lines of the game log."""
    return {"summary": summary(state), "log_tail": list(log)[-LOG_TAIL:]}


def compare_expected(expected, state, log):
    """[] when a replay ended the way expected.json says, else readable differences."""
    got = expectation(state, log)
    diffs = differences(expected.get("summary") or {}, got["summary"])
    want, have = list(expected.get("log_tail") or []), got["log_tail"]
    n = min(len(want), len(have))
    if n and want[-n:] != have[-n:]:
        pairs = list(zip(reversed(want[-n:]), reversed(have[-n:])))
        k = next(i for i, (a, b) in enumerate(pairs) if a != b)
        diffs.append(f"the game log differs {k + 1} line{'s' if k else ''} from the end: expected {pairs[k][0]!r}, got {pairs[k][1]!r}")
    elif want and not have:
        diffs.append("the replay produced no game log")
    return diffs


def save_regression(report_path, name, note="", runtime=None, as_recorded=False, root=None, progress=None):
    """Replay a bug report and keep it (the zip and what the replay ended on) as a regression test in tests/reports/NAME/.
    The expectation is what the replay produces today, so the test guards against later changes; with as_recorded=True it is what the report
    itself recorded, and the save is refused unless the replay matches that."""
    root = root or REGRESSION_DIR
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,60}", name or ""):
        raise ReportError("the name may hold only small letters, digits and _ (for example arc_lightning_divide)")
    report = load_report(report_path)
    if not report.complete:
        raise ReportError("this report holds only the newest clicks (the game was longer than one report keeps), so it cannot be replayed from the start")
    result = replay(report, runtime, progress=progress)
    if result["diverged"]:
        raise ReportError(f"the replay diverged after {result['sent']} clicks: {result['diverged'][1]}")
    if as_recorded:
        if result["differences"]:
            raise ReportError("the replay does not match the report's board, so it cannot be saved as recorded:\n  - " + "\n  - ".join(result["differences"]))
        base_log = report.log_lines or result["log"]
        expected = expectation(report.state, base_log)
    else:
        expected = expectation(result["state"], result["log"])
    dest = os.path.join(root, name)
    if os.path.exists(dest):
        raise ReportError(f"{dest} already exists; choose another name (or delete that folder to replace it)")
    os.makedirs(dest)
    import shutil
    shutil.copyfile(report_path, os.path.join(dest, "report.zip"))
    meta = {"name": name, "note": note, "seed": report.seed, "clicks": len(report.commands), "as_recorded": bool(as_recorded)}
    meta.update(expected)
    with open(os.path.join(dest, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    return meta


def saved_names(root=None):
    root = root or REGRESSION_DIR
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n, "report.zip")) and os.path.isfile(os.path.join(root, n, "expected.json")))


def check_saved(name, runtime=None, root=None, progress=None):
    """Replay one saved game. Returns (problems, meta): problems is [] when it played out the same."""
    root = root or REGRESSION_DIR
    with open(os.path.join(root, name, "expected.json"), encoding="utf-8") as f:
        meta = json.load(f)
    report = load_report(os.path.join(root, name, "report.zip"))
    result = replay(report, runtime, progress=progress)
    if result["diverged"]:
        return [f"the replay diverged after {result['sent']} clicks: {result['diverged'][1]}"], meta
    return compare_expected(meta, result["state"], result["log"]), meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a Manticore bug report against the same shuffle and the same clicks.")
    ap.add_argument("report", nargs="?", help="the bugreport_*.zip")
    ap.add_argument("--runtime", default=None, help="folder holding forge.jar (default: the game's forge_runtime)")
    ap.add_argument("--upto", type=int, default=None, help="send only the first N clicks")
    ap.add_argument("--log", action="store_true", help="print the replay's game log")
    ap.add_argument("--json", default=None, help="write the board the replay ended on to this file")
    ap.add_argument("--save-as", metavar="NAME", help="keep this game as a regression test in tests/reports/NAME/")
    ap.add_argument("--note", default="", help="with --save-as: a line saying what the game guards (for example 'trample damage goes to the player')")
    ap.add_argument("--as-recorded", action="store_true", help="with --save-as: expect what the REPORT recorded (refused when the replay differs)")
    ap.add_argument("--check-saved", nargs="?", const="all", metavar="NAME", help="replay the saved regression games (all, or one) and report which changed")
    args = ap.parse_args(argv)
    if args.check_saved:
        names = saved_names() if args.check_saved == "all" else [args.check_saved]
        if not names:
            print("No saved games yet (tests/reports is empty). Save one with:  python replay.py report.zip --save-as NAME")
            return 0
        bad = 0
        for n in names:
            try:
                problems, meta = check_saved(n, args.runtime)
            except (OSError, ValueError, ReportError) as e:
                problems, meta = [f"cannot check: {e}"], {}
            print(f"{'OK   ' if not problems else 'DIFFERS'} {n}" + (f"  - {meta.get('note')}" if meta.get("note") else ""))
            for p in problems:
                print("     -", p)
            bad += bool(problems)
        return 1 if bad else 0
    if not args.report:
        ap.error("give the bug report zip (or --check-saved)")
    if args.save_as:
        try:
            meta = save_regression(args.report, args.save_as, args.note, args.runtime, args.as_recorded)
        except ReportError as e:
            print(f"Not saved: {e}")
            return 2
        print(f"Saved tests/reports/{args.save_as}/ ({meta['clicks']} clicks, seed {meta['seed']}). It runs with the other tests; to update it after a "
              "deliberate change, delete that folder and save it again.")
        return 0
    try:
        report = load_report(args.report)
    except ReportError as e:
        print(f"Cannot replay: {e}")
        return 2
    print(f"Seed {report.seed}; {len(report.commands)} clicks; player {report.name}; decks: {', '.join(sorted(report.decks))}")
    if not report.complete and args.upto is None:
        print("Warning: the report holds only the newest clicks (the game was longer than one report keeps): the replay may diverge.")
    last = [0]

    def progress(done, total):
        if done == total or done - last[0] >= 25:
            last[0] = done
            print(f"  sent {done}/{total}", flush=True)
    result = replay(report, args.runtime, args.upto, progress)
    if args.log:
        print("\n".join(result["log"]))
    if args.json and result["state"]:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result["state"], f, indent=1)
    if result["diverged"]:
        i, why = result["diverged"]
        print(f"DIVERGED after {result['sent']} clicks: {why}")
        return 1
    st = result["state"] or {}
    print(f"Replay finished: turn {st.get('turn')}, {st.get('phaseLabel')}; prompt: {(st.get('prompt', {}).get('message') or '').splitlines()[0] if st else ''}")
    if args.upto is None and report.state:
        if result["differences"]:
            print("The replay does NOT match the report's board:")
            for d in result["differences"]:
                print("  -", d)
            return 1
        print("REPRODUCED: the board matches the report's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
