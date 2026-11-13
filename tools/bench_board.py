# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/bench_board.py - round PERF1 (Karl, 3 Nov 2026: "Waiting after I act" on a big board). How long the game takes, step by
step, from the player's click to the next question, on boards of about 60, 150 and 300 permanents.

    python tools/bench_board.py                         60, 150 and 300 permanents, 10 runs each (about 15-25 minutes)
    python tools/bench_board.py --sizes 150 --runs 3    one board size, 3 runs
    python tools/bench_board.py --out bench_out         also write bench_out/bench_board.json and bench_board.txt

Each run sets the board up again (Forge's Setup Game State, the bridge started with --dev and --timing), then times three
actions on it, each from the moment the command is sent to the moment the next question's snapshot has been read:
  cast    - click Ornithopter (no mana to pay) in Main 1: it resolves, the Soul Warden triggers, and Main 1 asks again
  pass    - OK in Main 1 with an empty stack: the game goes to combat and asks for attackers
  attack  - five creatures clicked as attackers, then OK (only the OK is timed): the AI blocks, damage, then Main 2 asks

The bridge's timing lines (java_bridge Timing.java) split each wait into: the command reaching the bridge (rx), waiting for
Swing (queued), Main.handle, Patch 43's Passing.decide and its hasAnswer, Forge's own chooseSpellAbilityToPlay for my seat (its
AvailableActions scan, every priority I had, passed by itself or asked), the AI's decisions, building / writing the snapshots
(and the last one's size), and on this side reading the last snapshot (json). "rest" is what is left: Forge's rules work
(resolving, triggers, state-based actions, combat) plus the bridge's 40 ms snapshot delay - an estimate, since some steps run
on other threads at the same time. The table's first frame on the new snapshot is timed afterwards, headless (pygame dummy
driver), on the recorded snapshot of each board size.

Never used by the game. The engine log of each size goes to the output folder (or a temporary one).
"""
import argparse
import json
import os
import queue
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forge_client as fc          # noqa: E402

ACTIONS = ("cast", "pass", "attack")
SIZES = (60, 150, 300)
LIBRARY = ["Forest"] * 30
STEP_LIMIT = 120.0
OLD_WAY = ["--skip-scan", "off", "--question-flush", "off"]     # before round PERF1, inside the same bridge          # seconds one action may take before the run is an error

# The human's side: Lathril's elves (the sample deck's own token: 1/1 Elf Warrior), Elvish Archdruid and Imperious Perfect as
# lords with abilities, Glorious Anthem, Soul Warden (a trigger for every creature), Forests. The AI's side: Adeline's 1/1
# Humans, Intangible Virtue, Glorious Anthem, Plains. My hand has spells that target (Lightning Bolt, Swords, Beast Within, Giant
# Growth) and Prodigal Pyromancer pings: for those Forge's scan looks over the whole board. About two thirds of the permanents are mine (Karl: "when I have a lot of
# creatures").
MY_FIXED = ["Soul Warden", "Elvish Archdruid", "Elvish Archdruid", "Imperious Perfect", "Glorious Anthem", "Elvish Mystic",
            "Llanowar Elves", "Prodigal Pyromancer", "Mountain", "Mountain", "Plains", "Plains"]
AI_FIXED = ["Glorious Anthem", "Intangible Virtue"]
MY_TOKEN = "T:g_1_1_elf_warrior"
AI_TOKEN = "T:w_1_1_human"
HAND = ["Ornithopter"] * 3 + ["Llanowar Elves", "Elvish Visionary", "Giant Growth", "Lightning Bolt", "Swords to Plowshares",
                               "Beast Within"]                # spells that target: Forge's scan looks at every target


def board(size):
    """(my battlefield, the AI's battlefield) for a board of about `size` permanents."""
    mine_n = round(size * 0.65)
    ai_n = size - mine_n
    my_lands = ["Forest"] * max(6, size // 15)
    ai_lands = ["Plains"] * max(5, size // 20)
    mine = MY_FIXED + my_lands
    mine += [MY_TOKEN] * max(0, mine_n - len(mine))
    ai = AI_FIXED + ai_lands
    ai += [AI_TOKEN] * max(0, ai_n - len(ai))
    return mine, ai


def setup_lines(size):
    mine, ai = board(size)
    return ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=5", "humanlandsplayed=0",
            "humanhand=" + ";".join(HAND), "humanbattlefield=" + ";".join(mine), "humanlibrary=" + ";".join(LIBRARY),
            "humangraveyard=", "humanexile=", "aihand=", "ailibrary=" + ";".join(LIBRARY), "aibattlefield=" + ";".join(ai),
            "aigraveyard=", "aiexile=", "removesummoningsickness=true",
            "humancommand=Lathril, Blade of the Elves|IsCommander", "aicommand=Adeline, Resplendent Cathar|IsCommander"]


class TimedSession(fc.ForgeSession):
    """ForgeSession whose reader thread stamps each line with when it was read and how long json.loads took."""
    jvm_extra = ()

    def java_command(self, java, cp):
        cmd = super().java_command(java, cp)
        return cmd[:1] + list(self.jvm_extra) + cmd[1:]

    def _read(self):
        try:
            for line in self.proc.stdout:
                t_rx = time.time()
                line = line.strip()
                if not line:
                    continue
                t0 = time.perf_counter()
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                m["_rx"] = t_rx
                m["_parse_ms"] = (time.perf_counter() - t0) * 1000
                m["_bytes"] = len(line)
                self.inbox.put(m)
        finally:
            self.inbox.put({"t": "_exit"})


class Bench:
    def __init__(self, deck, ai_deck, runtime=None, seed=7, say=print, folder=None, extra_args=()):
        self.deck, self.ai_deck, self.runtime, self.seed, self.say = deck, ai_deck, runtime, seed, say
        self.folder = folder
        self.extra_args = list(extra_args)
        self.jfr = False
        self.sample = False
        self.s = None
        self.snaps = []              # (time read, state) for every snapshot, newest last (kept short)

    def start(self, tag):
        fc.sync_bridge(self.runtime) if self.runtime else fc.sync_bridge()
        s = TimedSession(self.deck, [self.ai_deck], name="Bench", seed=self.seed, runtime=self.runtime, dev=True, timing=True)
        if self.jfr:
            s.jvm_extra = ["-XX:FlightRecorderOptions=stackdepth=512", "-XX:StartFlightRecording=filename=%s,settings=profile" % os.path.join(self.folder, "bench_%s.jfr" % tag)]
        extra = list(self.extra_args)
        if self.sample:
            extra += ["--sample", os.path.join(self.folder, "stacks_%s.txt" % tag)]
        if extra:
            base = s._bridge_args
            s._bridge_args = lambda: base() + extra
        s.stderr_path = os.path.join(self.folder, "engine_%s.log" % tag)
        s.start()
        self.s = s
        end = time.time() + 180
        while time.time() < end:
            self.pump()
            st = s.state
            if s.exited or s.fatal:
                raise RuntimeError(s.fatal or "Forge stopped while starting")
            if st and self.asking_priority(st):
                return
            self.answer_anything(st)
            time.sleep(0.05)
        raise RuntimeError("the game did not start within 180 seconds")

    def stop(self):
        if self.s:
            self.s.close()
            self.s = None

    def pump(self):
        s = self.s
        while True:
            try:
                m = s.inbox.get_nowait()
            except queue.Empty:
                return
            s.handle(m)
            if m.get("t") == "state":
                self.snaps.append(m)
                del self.snaps[:-4]

    @staticmethod
    def asking_priority(st):
        msg = ((st or {}).get("prompt") or {}).get("message", "")
        return bool(st) and st.get("asking") is not False and msg.startswith("Priority:")

    def answer_anything(self, st):
        """Before the board: keep the opening hand, answer anything else with its first option."""
        s = self.s
        if s.requests:
            req = s.requests[0]
            opts = req.get("options") or req.get("items") or []
            kind = req.get("kind")
            if kind == "confirm":
                s.answer(req, True)
            elif kind in ("order",):
                s.answer(req, list(range(len(opts))))
            elif kind in ("choose", "choose_optional"):
                s.answer(req, [0] if opts else [])
            else:
                s.answer(req, 0)
            return
        msg = ((st or {}).get("prompt") or {}).get("message", "")
        if msg and not msg.startswith("Priority:") and (st.get("prompt") or {}).get("ok"):
            s.ok()
            time.sleep(0.2)

    def wait_question(self, seq_before, version_before, limit=STEP_LIMIT):
        """Until a snapshot with a newer question (asking) has been read, or a request arrives. Returns (snapshot or None,
        the request or None, the time it was read)."""
        s = self.s
        end = time.time() + limit
        while time.time() < end:
            try:
                m = s.inbox.get(timeout=0.5)
            except queue.Empty:
                if s.exited or s.fatal:
                    raise RuntimeError(s.fatal or "Forge stopped")
                continue
            s.handle(m)
            t = m.get("t")
            if t == "state":
                self.snaps.append(m)
                del self.snaps[:-4]
                if m.get("asking") is not False and (m.get("inputSeq") or 0) > seq_before:
                    return m, None, m["_rx"], m
            elif t == "request":
                return s.state, m, m["_rx"], m
            elif t == "_exit":
                raise RuntimeError("Forge stopped")
        raise RuntimeError("no new question within %d s" % limit)

    def settle(self, seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            self.pump()
            time.sleep(0.02)

    def apply_board(self, size):
        s = self.s
        self.resolve_extras(s.state, None)
        done = s.setups_done
        s.setup(setup_lines(size))
        end = time.time() + 60
        while time.time() < end:
            self.pump()
            if s.setups_done > done:
                break
            time.sleep(0.05)
        # the set-up can ask (none of these cards do); then wait for a priority question on the new board
        end = time.time() + 60
        while time.time() < end:
            self.pump()
            st = s.state
            me = s.me()
            want = len(board(size)[0])
            if st and me and self.asking_priority(st) and abs(len(me["zones"].get("battlefield", [])) - want) <= 2 \
                    and not st.get("stack") and "MAIN1" in str(st.get("phase", "")).upper().replace(" ", ""):
                self.settle(0.5)
                return True
            if s.requests:
                self.answer_anything(st)
            time.sleep(0.05)
        return False

    def timed(self, action, send):
        """Send one command and time it to the next question. Returns a dict of step times (ms)."""
        s = self.s
        self.pump()
        s.timings.clear()
        seq = (s.state or {}).get("inputSeq") or 0
        ver = s.state_version
        t_send = time.time()
        send()
        st, req, t_rx, m = self.wait_question(seq, ver)
        t_end = time.time()
        time.sleep(0.15)                       # late timing lines (the snap line comes right after the snapshot)
        self.pump()
        return self.split(action, t_send, t_rx, m, list(s.timings)), st, req

    @staticmethod
    def split(action, t_send, t_rx, last, timings):
        total = (t_rx - t_send) * 1000
        lo, hi = t_send * 1000 - 5, t_rx * 1000 + 5
        win = [x for x in timings if lo <= x.get("at", 0) <= hi]
        out = {"action": action, "total": total, "t0": t_send * 1000, "t1": t_rx * 1000}
        rx = [x for x in win if x["k"] == "rx"]
        out["rx"] = (rx[0]["at"] - t_send * 1000) if rx else None
        h = [x for x in win if x["k"] == "handle"]
        out["queued"] = h[0].get("queued") if h else None
        out["handle"] = h[0]["ms"] if h else None
        dec = [x for x in win if x["k"] == "decide"]
        out["decide"] = sum(x["ms"] for x in dec)
        out["decide_n"] = len(dec)
        out["decisions"] = [x.get("d") for x in dec]
        ans = [x for x in win if x["k"] == "answer"]
        out["answer"] = sum(x["ms"] for x in ans)
        out["answer_n"] = len(ans)
        fg = [x for x in win if x["k"] == "forge"]
        out["forge"] = sum(x["ms"] for x in fg)
        out["forge_n"] = len(fg)
        out["forge_pass"] = sum(x["ms"] for x in fg if x.get("ret") == "pass")
        out["forge_ask"] = sum(x["ms"] for x in fg if x.get("ret") == "ask")
        out["forge_max"] = max([x["ms"] for x in fg] or [0])
        ai = [x for x in win if x["k"] == "ai"]
        out["ai"] = sum(x["ms"] for x in ai)
        out["ai_n"] = len(ai)
        sn = [x for x in win if x["k"] == "snap"]
        out["snap_build"] = sum(x["ms"] for x in sn)
        out["snap_json"] = sum(x.get("json", 0) for x in sn)
        out["snap_send"] = sum(x.get("send", 0) for x in sn)
        out["snap_n"] = len(sn)
        out["snap_bytes"] = (last or {}).get("_bytes")
        inp = [x for x in win if x["k"] == "input"]
        out["input_to_snap"] = (sn[-1]["at"] - inp[-1]["at"]) if (inp and sn) else None     # the question shown -> its snapshot written
        out["py_parse"] = (last or {}).get("_parse_ms")
        accounted = sum(v or 0 for v in (out["rx"], out["queued"], out["handle"], out["decide"], out["forge"], out["ai"],
                                          out["snap_build"], out["snap_json"], out["snap_send"], out["py_parse"]))
        out["rest"] = total - accounted
        return out

    def find(self, name, zone="hand"):
        me = self.s.me() or {}
        for c in me.get("zones", {}).get(zone, []):
            if c.get("name") == name:
                return c
        return None

    def attackers(self, n=5):
        me = self.s.me() or {}
        out = []
        for c in me.get("zones", {}).get("battlefield", []):
            if c.get("name") == "Elf Warrior" and not c.get("tapped") and not c.get("attacking"):
                out.append(c["id"])
            if len(out) >= n:
                break
        return out

    def run_once(self, size):
        """One run on a fresh board: cast, pass, attack. Returns [step dicts] (an action that couldn't be done is left out)."""
        s = self.s
        if not self.apply_board(size):
            raise RuntimeError("the board could not be set up")
        rows = []
        orn = self.find("Ornithopter")
        if orn:
            row, st, req = self.timed("cast", lambda: s.click_card(orn["id"]))
            row["stop_stack"] = len((st or {}).get("stack") or [])
            rows.append(row)
            self.resolve_extras(st, req)
        if self.asking_priority(s.state) and not (s.state or {}).get("stack"):
            row, st, req = self.timed("pass", s.ok)
            rows.append(row)
            msg = ((st or {}).get("prompt") or {}).get("message", "")
            if req is None and "attack" in msg.lower():
                for cid in self.attackers():
                    seq = (s.state or {}).get("inputSeq") or 0
                    s.click_card(cid)
                    self.settle(0.25)
                row, st, req = self.timed("attack", s.ok)
                rows.append(row)
                self.resolve_extras(st, req)
        return rows

    def resolve_extras(self, st, req):
        """Answer anything left over (a block-order question, a trigger order) so the next run starts clean."""
        s = self.s
        for _ in range(20):
            self.settle(0.2)
            if s.requests:
                self.answer_anything(s.state)
                continue
            st = s.state
            if self.asking_priority(st) and not st.get("stack"):
                return
            if self.asking_priority(st):
                s.ok()                                  # something of mine on the stack (the Soul Warden trigger): let it resolve
                continue
            if st and st.get("asking") is not False and not self.asking_priority(st):
                p = st.get("prompt") or {}
                if p.get("ok"):
                    s.ok()
                elif p.get("cancel"):
                    s.cancel()
        return


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


STEPS = [("total", "click to the next question"), ("rx", "the command reaching the bridge"), ("queued", "waiting for Swing"),
         ("handle", "Main.handle"), ("decide", "Passing.decide (patch 43)"), ("answer", "  of it: hasAnswer"),
         ("forge", "Forge's chooseSpellAbilityToPlay for me (AvailableActions)"), ("forge_pass", "  of it: priorities passed by itself"),
         ("forge_ask", "  of it: the priority that asked"), ("ai", "the AI's decisions"), ("snap_build", "building snapshots"),
         ("snap_json", "snapshots to text"), ("snap_send", "writing snapshots"), ("py_parse", "Python reading the last one (json)"),
         ("input_to_snap", "the question shown -> its snapshot written (in the above)"),
         ("rest", "rest: Forge's rules work + the 40 ms delay (estimate)")]


def table(rows, size):
    lines = ["Board of %d permanents (medians of %d runs; ms)" % (size, len([r for r in rows if r["action"] == "pass"]))]
    head = "%-62s" % "step" + "".join("%12s" % a for a in ACTIONS)
    lines.append(head)
    for key, label in STEPS:
        vals = []
        for a in ACTIONS:
            v = median([r.get(key) for r in rows if r["action"] == a])
            vals.append("%12s" % ("-" if v is None else ("%.1f" % v)))
        lines.append("%-62s" % label + "".join(vals))
    for key, label in [("forge_n", "my priorities Forge looked at (count)"), ("ai_n", "AI decisions (count)"),
                       ("snap_n", "snapshots sent (count)"), ("snap_bytes", "last snapshot size (bytes)"),
                       ("forge_max", "the longest single Forge look (ms)")]:
        vals = []
        for a in ACTIONS:
            v = median([r.get(key) for r in rows if r["action"] == a])
            vals.append("%12s" % ("-" if v is None else ("%d" % v)))
        lines.append("%-62s" % label + "".join(vals))
    return lines


def timeouts_in(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if "AvailableActions: heuristic timed out" in ln)
    except OSError:
        return 0


def frame_times(snapshot, runs=10, size=(1920, 1080)):
    """The table's first frame after a new snapshot arrives (headless). Returns the median ms, or None without pygame."""
    try:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import copy
        import forge_table as ft
        from tests.forge_fake import FakeSession, StubStore
    except Exception:
        return None
    st = {k: v for k, v in snapshot.items() if not k.startswith("_")}
    sess = FakeSession(copy.deepcopy(st), [])
    gui = ft.ForgeTable(sess, StubStore(), window_size=size)
    for _ in range(3):
        gui.sync()
        gui.render()
    times = []
    for i in range(runs):
        new = copy.deepcopy(st)
        new["inputSeq"] = (new.get("inputSeq") or 0) + i + 1
        sess.handle(new)
        t0 = time.perf_counter()
        gui.sync()
        gui.render()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def default_out():
    """soak_runs/bench_board in a portable copy (Karl's git checkout: soak_runs/ is the folder .gitignore and backup.py already
    leave out - round 28bb's lesson), the per-user local folder's bench_board/ in an installed one."""
    try:
        import paths
        if paths.is_portable():
            return os.path.join(ROOT, "soak_runs", "bench_board")
        return os.path.join(paths.local_dir(), "bench_board")
    except Exception:
        return tempfile.mkdtemp(prefix="bench_board_")


def main(argv=None):
    ap = argparse.ArgumentParser(description="PERF1: the wait after the player acts, step by step, on big boards")
    ap.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", help="a folder for bench_board.json / bench_board.txt / the engine logs")
    ap.add_argument("--runtime")
    ap.add_argument("--sample", action="store_true", help="sample the game threads' stacks every 10 ms (profiling)")
    ap.add_argument("--jfr", action="store_true", help="record a Java Flight Recording of each board size (profiling)")
    ap.add_argument("--both", action="store_true", help="each board twice: the old way (--skip-scan off --question-flush off), then the new")
    ap.add_argument("--bridge-arg", action="append", default=[], help="an extra bridge argument (tests / comparisons)")
    a = ap.parse_args(argv)
    print("PERF1 bench: the output goes to %s" % (a.out or default_out()), flush=True)
    folder = a.out or default_out()
    os.makedirs(folder, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="bench_decks_")
    deck = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Lathril, Blade of the Elves"], ["Forest"] * 99, "Bench")
    ai = fc.write_deck_file(os.path.join(tmp, "ai.dck"), ["Adeline, Resplendent Cathar"], ["Plains"] * 99, "BenchAI")
    extra = []
    for x in a.bridge_arg:
        extra += x.split()
    results = {"version": None, "sizes": {}, "args": vars(a)}
    try:
        import version
        results["version"] = version.describe()
    except Exception:
        pass
    report = []
    print("PERF1 bench: %s" % results["version"])
    java = fc.find_java()                            # round ARM1: which Java was timed, and whether it is native (aarch64)
    results["java"] = {"path": java, "arch": fc.java_arch(java) if java else "", "major": fc.java_major(java) if java else 0,
                       "override": bool(os.environ.get(fc.JAVA_OVERRIDE_ENV, "").strip())}
    java_line = "Java: %s (os.arch %s, Java %s%s)" % (java, results["java"]["arch"] or "?", results["java"]["major"],
                                                      ", from MANTICORE_JAVA" if results["java"]["override"] else "")
    print("PERF1 bench: " + java_line, flush=True)
    configs = [("", extra)]
    if a.both:                                      # Forge's / the old way first, in the same bridge, then the new way
        configs = [("old", extra + OLD_WAY), ("new", extra)]
    for size in [int(x) for x in a.sizes.split(",") if x.strip()]:
        for label, args in configs:
            key = "%d" % size + ("/" + label if label else "")
            tag = key.replace("/", "_")
            b = Bench(deck, ai, runtime=a.runtime, seed=a.seed, folder=folder, extra_args=args)
            b.jfr = a.jfr
            b.sample = a.sample
            rows, errors = [], []
            t0 = time.time()
            last = None
            try:
                b.start(tag)
                for i in range(a.runs):
                    try:
                        got = b.run_once(size)
                        rows += got
                        print("  %s permanents, run %d: %s" % (key, i + 1, "  ".join(
                            "%s %.0f ms" % (r["action"], r["total"]) for r in got)), flush=True)
                    except Exception as exc:
                        errors.append("%s: %s" % (type(exc).__name__, exc))
                        print("  %s permanents, run %d: ERROR %s" % (key, i + 1, errors[-1]), flush=True)
                        b.stop()
                        b.start("%s_r%d" % (tag, i))
                last = b.snaps[-1] if b.snaps else None
            finally:
                b.stop()
            tmo = timeouts_in(os.path.join(folder, "engine_%s.log" % tag))
            frame = frame_times(last) if last else None
            mine, theirs = board(size)
            results["sizes"][key] = {"rows": rows, "errors": errors, "timeouts": tmo, "frame_ms": frame,
                                     "permanents": len(mine) + len(theirs), "seconds": time.time() - t0}
            lines = table(rows, size)
            if label:
                lines[0] += "  -  " + ("the old way (--skip-scan off --question-flush off)" if label == "old" else "the new way")
            lines.append("%-62s%12s" % ("the table's first frame on the new snapshot, 1920x1080 (ms)", "-" if frame is None else "%.1f" % frame))
            lines.append("AvailableActions timeouts in the engine log: %d     errors: %d" % (tmo, len(errors)))
            report += lines + [""]
            print("\n".join(lines) + "\n", flush=True)
            if last:
                with open(os.path.join(folder, "last_snapshot_%s.json" % tag), "w", encoding="utf-8") as f:
                    json.dump({k: v for k, v in last.items() if not k.startswith("_")}, f)
    with open(os.path.join(folder, "bench_board.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    with open(os.path.join(folder, "bench_board.txt"), "w", encoding="utf-8") as f:
        f.write("PERF1 bench: %s\n%s\n\n" % (results["version"], java_line) + "\n".join(report) + "\n")
    print("written: %s" % folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
