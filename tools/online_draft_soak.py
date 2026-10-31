# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\online_draft_soak.py - Round CD5: friends draft a Commander cube together online, then play the 4-player game, again and
again - bots at every seat, through the real NetHost (--draft) on 127.0.0.1 and real NetSession friends.

    python tools\\online_draft_soak.py --hours 0.5 [--drafts N] [--seed N] [--friends 1-3] [--drops 0.5] [--takeover 0.5]
                                        [--bad 0.3] [--no-game] [--keep] [--out DIR]

Each draft: the host's HostSession with --draft; 1-3 friends join (NetSession, draft=True) and the AI drafters fill the seats;
online_draft.HostDraft runs the draft on the host and each friend's GuestPod follows it, exactly as the table does. Every seat is
drafted by draft_ai's chooser (a different seed per seat). Then each person's deck is Auto-built, sent, checked, and the
4-player Commander Draft game is played by soak_bot.SoakBot at every person's seat (tools/online_soak.Seat), the AI drafters as
Forge AI players, until game over (one bot concedes after a random turn - two bots rarely finish a game).

Rules (FAIL unless said):
  draft_leak        a friend was sent a pack that wasn't the one in front of them, a pool that wasn't theirs, a review with
                    another person's picks, or anything with the draft's seed (checked at send time against the host's draft)
  draft_refused     a bot's honest pick or deck was refused
  draft_bad_taken   a bad pick (planted by --bad: another seat's card, one card too few, an old pick) was taken
  draft_drop        a friend's cut connection didn't pause the draft, or the friend wasn't back and drafting within 60 s
  draft_takeover    the AI didn't pick for a friend's seat the host gave it, or the friend didn't get the seat back
  draft_stall       nothing happened in the draft for 120 s
  draft_rejudge     the friends' recordings, re-read after the game, don't agree with the host's saved draft (see rejudge())
  game_start        the game didn't start on every table within 120 s of the last deck
  and the online soak's game rules: online_hidden, online_refused, online_left, online_mismatch, stall, and bridge_rules.
Output: soak_runs\\online_draft_<date>_<time>\\ - draft_summary.txt, and draft_NNN\\ (the recordings, the pod, the engine log)
kept for drafts with a finding (all with --keep).
"""
import argparse
import datetime
import json
import os
import random
import shutil
import socket
import sys
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

PASSWORD = "badger-moss-quay-17"
HOST_READY_SECONDS = 150
JOIN_SECONDS = 30
DRAFT_STALL_SECONDS = 120
BACK_SECONDS = 60
GAME_START_SECONDS = 120
POLL = 0.02
TURN_CAP = 60
GAME_TIMEOUT = 25 * 60

osoak = fc = od = None


def _load():
    global osoak, fc, od
    if fc is None:
        from tools import online_soak as _os
        _os._load_modules()
        import forge_client as _fc
        import online_draft as _od
        osoak, fc, od = _os, _fc, _od


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Blocker:
    """Keeps a friend's table from reconnecting until `until` (a cut connection that stays down: the friend is away)."""

    def __init__(self):
        self.until = 0.0

    def __call__(self, *a, **k):
        if time.monotonic() < self.until:
            raise OSError("the soak keeps this friend away")
        import forge_net
        return forge_net.connect_tls(*a, **k)


class Friend:
    """One friend's table: its NetSession, its GuestPod, its drafting bot, and what the host sent it (for draft_leak)."""

    def __init__(self, k, name, session, seed):
        self.k, self.name, self.session = k, name, session
        self.blocker = Blocker()
        session._connect = self.blocker
        self.pod = od.GuestPod(od.guest_sender(session))
        self.seed = seed
        self.chooser = None
        self.deck_sent = False
        self.bad_sent = 0

    def pump(self):
        return od.pump_guest(self.session, self.pod)


class DraftResult:
    def __init__(self, index, seed, friends):
        self.index, self.seed, self.friends = index, seed, friends
        self.findings = []
        self.picks = 0
        self.refused_bad = 0
        self.drops = self.backs = 0
        self.takeovers = 0
        self.ended = "?"
        self.turns = 0
        self.seconds = 0.0
        self.game = None
        self.kept = None
        self.messages = 0

    def fail(self, rule, detail):
        self.findings.append({"rule": rule, "severity": "fail", "detail": str(detail)[:400]})

    def line(self):
        fails = sorted({f["rule"] for f in self.findings if f["severity"] == "fail"})
        warns = sorted({f["rule"] for f in self.findings if f["severity"] == "warn"})
        return ("draft %d: seed=%s friends=%d picks=%d messages=%d bad_refused=%d drops=%d/%d takeovers=%d game=%s turns=%d "
                "%.0fs fails=%s warns=%s%s" % (self.index, self.seed, self.friends, self.picks, self.messages, self.refused_bad,
                                                self.backs, self.drops, self.takeovers, self.ended, self.turns, self.seconds,
                                                ",".join(fails) or "-", ",".join(warns) or "-",
                                                ("  -> " + self.kept) if self.kept else ""))


def leak_checker(result, hd_holder):
    """HostDraft's `send`, wrapped: every message is checked against the host's draft as it is when it is sent (903.13d)."""
    def check(net_seat, msg, real_send):
        hd = hd_holder[0]
        result.messages += 1
        text = json.dumps(msg)
        if '"seed"' in text:
            result.fail("draft_leak", "a message to seat %s carries the seed" % net_seat)
        if hd is not None:
            d = hd.seat_of.get(net_seat)
            draft = hd.pod.draft
            if msg.get("k") == "view":
                if msg.get("pack") != draft.pack_for(d):
                    result.fail("draft_leak", "seat %s was sent a pack that isn't the one in front of it" % net_seat)
                if msg.get("pool") != list(draft.pools[d]):
                    result.fail("draft_leak", "seat %s was sent a pool that isn't its own" % net_seat)
            if msg.get("k") == "review":
                people = {s for s in range(draft.n) if draft.seats[s].kind != "ai"}
                bad = {p[0] for p in msg.get("picks") or []} & (people - {d})
                if bad:
                    result.fail("draft_leak", "seat %s's review shows another person's picks (%s)" % (net_seat, sorted(bad)))
        return real_send(net_seat, msg)
    return check


def rejudge(host_record, friend_records, pod_folder):
    """Re-read the recordings after the fact: every view a friend received must show, for its pick (round, turn), the pack its
    seat had then in the host's saved draft (draft.json's log, played back on the seed). Returns [problem]."""
    import draft_pod as dp
    pod = dp.Pod.load(pod_folder)
    seat_by_name = {pod.seat_name(s): s for s in range(pod.draft.n)}
    before = {(p.seat, p.round, p.turn): sorted(p.pack_before) for p in pod.draft.log}
    pools = {}
    for p in pod.draft.log:
        pools.setdefault(p.seat, []).extend(p.cards)
    problems = []
    for name, path in friend_records.items():
        seat = seat_by_name.get(name)
        if seat is None:
            problems.append("%s isn't a seat of the saved draft" % name)
            continue
        views = 0
        for line in _lines(path):
            if line.get("t") != "draft":
                continue
            msg = line.get("msg") or {}
            if msg.get("k") != "view" or msg.get("pack") is None:
                continue
            views += 1
            key = (seat, msg.get("round"), (msg.get("turn") or 0) + 1)
            if key not in before:
                if pod.draft.done:                   # (a draft cut short: its last views were never picked from)
                    problems.append("%s: a view for round %s pick %s, which the saved draft never had" % (name, key[1], key[2]))
            elif sorted(msg["pack"]) != before[key]:
                problems.append("%s: round %s pick %s shows a pack the saved draft says it didn't have" % (name, key[1], key[2]))
            mine = pools.get(seat, [])
            if not set(msg.get("pool") or []) <= set(mine):
                problems.append("%s: a pool with cards its seat never drafted" % name)
        if not views:
            problems.append("%s: no views in its recording" % name)
    return problems


def _lines(path):
    import gzip
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            for raw in f:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue
    except OSError:
        return


def run_draft(index, seed, out_root, cert, friends=2, drop=False, takeover=False, bad=False, play=True, rng=None,
              keep=False, cube_key="cm", log=print):
    """One online draft (and its game). Returns a DraftResult."""
    _load()
    import cube_source as cs
    import draft_ai as da
    import draft_pod as dp
    rng = rng or random.Random(seed)
    res = DraftResult(index, seed, friends)
    t0 = time.time()
    folder = os.path.join(out_root, "draft_%03d" % index)
    os.makedirs(folder, exist_ok=True)
    host_rec = os.path.join(folder, "host.jsonl")
    recs = {}
    names = ["Sam", "Ann", "Lee"][:friends]
    host = None
    fr = []
    hd_holder = [None]
    try:
        tmp = os.path.join(folder, "decks")
        os.makedirs(tmp, exist_ok=True)
        port = free_port()
        host = fc.HostSession(os.path.join(tmp, "draft_host.dck"), "Karl", port, PASSWORD, cert, os.path.join(tmp, "incoming.dck"),
                              upnp=False, bind="127.0.0.1", code="draftsoak", seed=seed, record_path=host_rec, draft=True,
                              guests=3)
        host.stderr_path = os.path.join(folder, "host_engine.log")
        host.start()
        end = time.monotonic() + HOST_READY_SECONDS
        while host.hosting is None and not host.exited and not host.host_failed and time.monotonic() < end:
            host.poll()
            time.sleep(0.05)
        if host.hosting is None:
            res.fail("host_failed", host.host_failed or "NetHost never got ready")
            return res
        for k, name in enumerate(names):
            recs[name] = os.path.join(folder, "friend_%s.jsonl" % name)
            g = fc.NetSession("127.0.0.1", port, name, PASSWORD, "", "", fingerprint=cert.fingerprint, code="draftsoak",
                              record_path=recs[name], draft=True)
            f = Friend(k, name, g, seed * 31 + k + 1)
            fr.append(f)
            g.start()
            if not g.draft:
                res.fail("join_failed", "%s's welcome didn't say draft" % name)
                return res
        end = time.monotonic() + JOIN_SECONDS
        while len(host.guests_joined) < friends and time.monotonic() < end:
            host.poll()
            time.sleep(0.05)
        if len(host.guests_joined) < friends:
            res.fail("join_failed", "the host saw %s joined" % host.guests_joined)
            return res
        host.draft_lock()
        end = time.monotonic() + JOIN_SECONDS
        while host.draft_locked is None and time.monotonic() < end:
            host.poll()
            time.sleep(0.02)
        seats = [(int(o["seat"]), o["name"]) for o in (host.draft_locked or {}).get("seats") or []]
        if len(seats) != friends:
            res.fail("join_failed", "draft_locked: %s" % host.draft_locked)
            return res
        cube, _note = cs.load(cube_key)
        check = cs.check_forge(cube.cards, cs.forge_script_lookup())
        pod = dp.Pod.new(cube, check, seed=seed, root=os.path.join(folder, "pods"),
                         seats=od.HostDraft.seats_for("Karl", [n for _s, n in seats]), online={"friends": [n for _s, n in seats]})
        checker = leak_checker(res, hd_holder)
        hd = od.HostDraft(pod, seats, lambda ns, msg: checker(ns, msg, host.draft_send))
        hd_holder[0] = hd
        hd.start()
        host_choose = da.chooser(pod.cards, seed + 5, pod.z)
        drop_at = rng.randint(4, 25) if drop and fr else None
        dropped = None                    # (friend, cut at, blocked until, given to the AI?)
        last = time.monotonic()
        bad_left = int(bad)                   # planted bad picks, at the first chances (each must be refused)
        while hd.stage == "pick":
            moved = od.pump_host(host, hd)
            host.poll()
            for f in fr:
                f.session.poll()
                moved += f.pump()
            if pod.draft.pack_for(0) and not hd.paused_on():
                hd.host_pick(host_choose(0, pod.draft.pack_for(0), pod.pool(0), pod.draft.needed(0)))
                res.picks += 1
                moved += 1
            for f in fr:
                gp = f.pod
                if dropped and dropped[0] is f and not f.session.connected():
                    continue
                if gp.can_pick() and gp.ready_to_show:
                    if f.chooser is None:
                        f.chooser = da.chooser(gp.cards, f.seed, gp.z)
                    if bad_left:
                        bad_left -= 1
                        plant = rng.choice(["short", "stale", "other"])
                        v = gp.view
                        if plant == "short":
                            msg = {"k": "pick", "cards": gp.pack()[:1], "at": [v["round"], v["turn"]]}
                        elif plant == "stale":
                            msg = {"k": "pick", "cards": gp.pack()[:2], "at": [v["round"], v["turn"] - 1]}
                        else:
                            mine = set(gp.pack())
                            others = [i for i in range(len(gp.cards)) if i not in mine and i not in gp.pool()]
                            msg = {"k": "pick", "cards": [gp.pack()[0], others[0]], "at": [v["round"], v["turn"]]}
                        before = len(pod.draft.pools[hd.seat_of[seats[f.k][0]]])
                        f.session.send(c="draft", msg=msg)
                        f.bad_sent += 1
                        end = time.monotonic() + 10
                        while gp.refused is None and time.monotonic() < end:
                            od.pump_host(host, hd)
                            host.poll()
                            f.session.poll()
                            f.pump()
                            time.sleep(0.02)
                        if gp.refused is None or len(pod.draft.pools[hd.seat_of[seats[f.k][0]]]) != before:
                            res.fail("draft_bad_taken", "%s's %s pick was taken (refused=%r)" % (f.name, plant, gp.refused))
                        else:
                            res.refused_bad += 1
                        gp.refused = None
                        continue
                    gp.pick(f.chooser(0, gp.pack(), gp.pool(), gp.needed()))
                    res.picks += 1
                    moved += 1
            # a friend drops: the draft must pause; the host may give the seat to the AI; then the friend comes back
            if drop_at is not None and dropped is None and res.picks >= drop_at:
                f = rng.choice(fr)
                f.blocker.until = time.monotonic() + (8 if takeover else 4)
                try:
                    f.session.proc.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                dropped = [f, time.monotonic(), False, False]
                res.drops += 1
            if dropped is not None:
                f, cut, paused_seen, given = dropped
                d = hd.seat_of[seats[f.k][0]]
                if not paused_seen and d in hd.paused_on():
                    dropped[2] = paused_seen = True
                if takeover and paused_seen and not given and time.monotonic() - cut > 2:
                    n0 = len(pod.draft.pools[d])
                    hd.give_to_ai(d)
                    dropped[3] = True
                    res.takeovers += 1
                    if len(pod.draft.pools[d]) == n0 and pod.draft.pack_for(d) is None and hd.stage == "pick":
                        pass                       # nothing in front of that seat yet: the AI picks when a pack comes
                if f.session.connected() and hd.connected.get(d) and time.monotonic() - cut > 1:
                    if not paused_seen and not given:
                        res.fail("draft_drop", "%s's connection was cut and the draft never paused" % f.name)
                    if takeover and hd.is_ai(d):
                        res.fail("draft_takeover", "%s is back but the AI still has the seat" % f.name)
                    res.backs += 1
                    dropped = None
                    drop_at = None
                elif time.monotonic() - cut > BACK_SECONDS:
                    res.fail("draft_drop", "%s wasn't back %d s after the cut (net_state=%s)" % (f.name, BACK_SECONDS,
                                                                                                 f.session.net_state))
                    return res
            if moved:
                last = time.monotonic()
            elif time.monotonic() - last > DRAFT_STALL_SECONDS:
                res.fail("draft_stall", "nothing for %d s: paused on %s, waiting for %s" % (
                    DRAFT_STALL_SECONDS, hd.paused_text(), [hd.name(s) for s in hd.waiting_for()]))
                return res
            time.sleep(POLL)
        planted = sum(f.bad_sent for f in fr)
        honest = [(d, text) for d, text in hd.refusals if not text.startswith("Waiting for")]   # a pick racing the pause is fine
        if len(honest) > planted:
            res.fail("draft_refused", "%d refusal(s) for %d planted bad pick(s): %s" % (len(honest), planted, honest[:4]))
        # ---- the decks -------------------------------------------------------------------------------------------------
        hd.host_deck(pod.build(0))
        end = time.monotonic() + 60
        while not hd.all_ready() and time.monotonic() < end:
            od.pump_host(host, hd)
            host.poll()
            for f in fr:
                f.session.poll()
                f.pump()
                gp = f.pod
                if gp.done and gp.stage_name == "build" and not f.deck_sent and gp.view and gp.cards:
                    deck = gp.build(0)
                    if deck is not None:
                        gp.send_deck(deck, by_hand=False)
                        f.deck_sent = True
            time.sleep(POLL)
        for f in fr:
            if f.pod.deck_state not in ("ok", None) and f.pod.deck_state != "sent":
                res.fail("draft_refused", "%s's deck: %s" % (f.name, f.pod.deck_state))
        if not hd.all_ready():
            res.fail("draft_stall", "the decks were never all ready: %s" % hd.ready())
            return res
        host_path, friend_paths, ai_paths, _labels = od.write_game_decks(hd, tmp)
        hd.to_game()
        host.draft_game(host_path, friend_paths, ai_paths)
        end = time.monotonic() + GAME_START_SECONDS
        while time.monotonic() < end and not (host.ready and all(f.session.ready for f in fr if not hd.is_ai(hd.seat_of[seats[f.k][0]]))):
            host.poll()
            for f in fr:
                f.session.poll()
            if host.draft_game_failed:
                break
            time.sleep(0.05)
        if not host.ready:
            res.fail("game_start", "the game never started (%s)" % (host.draft_game_failed or "no ready"))
            return res
        if not play:
            res.ended = "started"
            return res
        # ---- the game ------------------------------------------------------------------------------------------------------
        playing = [f for f in fr if not hd.is_ai(hd.seat_of[seats[f.k][0]])]
        tables = [osoak.Seat("host", host, seed)] + [osoak.Seat(f.name, f.session, seed + 7919 * (f.k + 1)) for f in playing]
        concede_turn = rng.randint(8, 30)
        g0 = time.time()
        last = time.monotonic()
        while True:
            progressed = False
            for t in tables:
                progressed = t.step() or progressed
            turn = (host.state or {}).get("turn") or 0
            res.turns = max(res.turns, turn)
            if concede_turn and turn >= concede_turn:
                # every person but one concedes (the AI drafters play on: the game ends when one side is left)
                # (when the host is the only person left at the table - its friends' seats went to the AI - the host concedes)
                for t in (tables if len(tables) == 1 else tables[1:] if rng.random() < 0.5 else tables[:-1]):
                    t.session.concede()
                concede_turn = None
            if host.game_over and all(t.session.game_over for t in tables):
                res.ended = "game_over"
                break
            if host.game_over:
                end = time.monotonic() + 15
                while time.monotonic() < end and not all(t.session.game_over for t in tables):
                    for t in tables:
                        t.session.poll()
                    time.sleep(0.05)
                if not all(t.session.game_over for t in tables):
                    res.fail("online_mismatch", "game over on %s only" % [t.label for t in tables if t.session.game_over])
                res.ended = "game_over"
                break
            for t in tables:
                if getattr(t.session, "peer_left", None) and not t.session.game_over:
                    res.fail("online_left", "%s saw peer_left %s mid-game" % (t.label, t.session.peer_left))
                    res.ended = "peer_left"
                if t.session.exited and not t.session.game_over:
                    res.ended = "crash_" + t.label
            if res.ended != "?":
                break
            if progressed:
                last = time.monotonic()
            if time.monotonic() - last > 120:
                res.fail("stall", "no new snapshot on any table for 120 s")
                res.ended = "stall"
                break
            if turn > TURN_CAP:
                res.ended = "turn_cap"
                break
            if time.time() - g0 > GAME_TIMEOUT:
                res.ended = "time_cap"
                break
            time.sleep(POLL)
        for t in tables:
            for leak in t.hidden:
                res.fail("online_hidden", "%s: %s" % (t.label, leak))
        for f in fr:
            for r in getattr(f.session, "refused_cmds", []) or []:
                res.fail("online_refused", json.dumps(r)[:200])
        res.game = {"players": 1 + len(playing) + len(ai_paths), "online": len(playing),
                    "life": {t.label: t.first_life for t in tables}}
        for t in tables:
            life = t.first_life or {}
            wrong = {n: v for n, v in life.items() if v != 40}
            if wrong:
                res.fail("online_life", "%s saw a starting life that isn't 40: %s" % (t.label, wrong))
        return res
    except Exception as e:
        import traceback
        res.fail("crash", "%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc()[-800:]))
        return res
    finally:
        for s in [f.session for f in fr] + ([host] if host else []):
            try:
                s.close()
            except Exception:
                pass
        res.seconds = time.time() - t0
        if hd_holder[0] is not None and not any(f["rule"] == "crash" for f in res.findings):
            try:
                for p in rejudge(host_rec, recs, hd_holder[0].pod.folder):
                    res.fail("draft_rejudge", p)
            except Exception as e:
                res.fail("draft_rejudge", "couldn't re-read: %s" % e)
        if res.findings or keep:
            res.kept = folder
        else:
            shutil.rmtree(folder, ignore_errors=True)


def summary_text(results, started, args):
    lines = ["ONLINE DRAFT SOAK (round CD5)", "started %s, %d draft(s), %.1f h asked" % (started, len(results), args.hours or 0), ""]
    fails = [r for r in results if any(f["severity"] == "fail" for f in r.findings)]
    lines.append("VERDICT: %s" % ("VALID - no failure" if not fails else "%d draft(s) with a failure" % len(fails)))
    games = [r for r in results if r.ended == "game_over"]
    lines.append("games to game over: %d of %d; picks %d; messages %d; bad picks refused %d; drops back %d of %d; takeovers %d" % (
        len(games), len(results), sum(r.picks for r in results), sum(r.messages for r in results),
        sum(r.refused_bad for r in results), sum(r.backs for r in results), sum(r.drops for r in results),
        sum(r.takeovers for r in results)))
    lines.append("")
    for r in results:
        lines.append(r.line())
        for f in r.findings:
            lines.append("    %s %s: %s" % (f["severity"].upper(), f["rule"], f["detail"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--hours", type=float, default=0.5)
    ap.add_argument("--drafts", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--friends", type=int, default=None, help="1-3 (default: random each draft)")
    ap.add_argument("--drops", type=float, default=0.5)
    ap.add_argument("--takeover", type=float, default=0.5)
    ap.add_argument("--bad", type=float, default=0.3)
    ap.add_argument("--no-game", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    _load()
    import forge_net as fn
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = args.out or os.path.join(BASE_DIR, "soak_runs", "online_draft_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    cert = fn.ensure_host_cert(out, fc.find_java())
    base = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 10 ** 6)
    deadline = time.time() + args.hours * 3600
    results = []
    i = 0
    while (args.drafts is None or i < args.drafts) and time.time() < deadline:
        rng = random.Random(base + i)
        friends = args.friends or rng.randint(1, 3)
        drop = rng.random() < args.drops
        r = run_draft(i, base + i, out, cert, friends=friends, drop=drop, takeover=drop and rng.random() < args.takeover,
                      bad=2 if rng.random() < args.bad else 0, play=not args.no_game, rng=rng, keep=args.keep)
        results.append(r)
        print(r.line(), flush=True)
        with open(os.path.join(out, "draft_summary.txt"), "w", encoding="utf-8") as f:
            f.write(summary_text(results, started, args))
        i += 1
    print(summary_text(results, started, args))
    return 1 if any(any(f["severity"] == "fail" for f in r.findings) for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
