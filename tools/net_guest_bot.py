# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/net_guest_bot.py - Round RLY1: join a real host from an invite code, as a friend's table would, and play a whole game with the
soak bot. For tests between two real machines: Karl's PC hosts (Host online), this runs somewhere else (the Oracle server, a
second PC) and joins it - directly, or through the relay when the invite code says so.

    python tools/net_guest_bot.py --invite-file FILE [--deck sample_decks/x.txt] [--name CloudBot] [--minutes 30]
                                  [--concede-turn N] [--out DIR] [--address HOST] [--port N]
    (or the invite code in the environment variable MANTICORE_INVITE instead of --invite-file)

--address / --port: join another address than the invite's, with the invite's password and certificate check - for a test from
the same house (the host's LAN address, 192.168.x.y) when the invite names the router's public address. The certificate check
still runs, so a wrong address fails as "not that PC", never as a game with someone else.

On a GitHub Actions runner (GITHUB_ACTIONS=true) the address and the password are masked in the run's log before anything is
printed (::add-mask::), so a public repository's log shows *** where they would be.

The invite code holds the game's password, so it is read from a file or the environment, never from the command line (where
process lists and shell histories show it), and it is never printed or written anywhere by this tool.

Output in --out: net_guest_result.json and net_guest_result.txt (the verdict, how far the connection got, the game's end, any
failing finding from bridge_rules on this table's own recording), connection_log.txt (round NETLOG1: every step of the
attempt with its time and exact error), guest.jsonl.gz (the recording).
Exit code: 0 the game was joined and reached game over (or was conceded at --concede-turn); 1 anything else.
"""
import argparse
import datetime
import json
import os
import random
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

JOIN_SECONDS = 90          # the hello, the welcome and (a game of 3-4) the lobby filling up
FIRST_STATE_SECONDS = 180  # the host's engine starting the game after everyone joined (Forge's start-up on Karl's PC: 10-20 s)
POLL_SLEEP = 0.02


def read_invite_text(path=None, env=os.environ):
    """The invite code from a file (its first non-empty line) or MANTICORE_INVITE; ValueError when there is none."""
    if path:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return line.strip()
        raise ValueError("the invite file %s is empty" % path)
    text = (env.get("MANTICORE_INVITE") or "").strip()
    if not text:
        raise ValueError("no invite code: give --invite-file FILE or set MANTICORE_INVITE")
    return text


def ci_mask(values, env=os.environ, out=sys.stdout):
    """On a GitHub Actions runner, tell the runner to hide these strings in the log (::add-mask::) before anything shows them."""
    if (env.get("GITHUB_ACTIONS") or "").lower() != "true":
        return 0
    n = 0
    for v in values:
        v = str(v or "").strip()
        if len(v) >= 4 and "\n" not in v:
            out.write("::add-mask::%s\n" % v)
            n += 1
    out.flush()
    return n


def pick_deck(fmt, rng, deck=None):
    if deck:
        return deck
    from tools import soak
    pool = soak.deck_pool("sample", fmt)
    if not pool:
        raise ValueError("no sample deck of the format %s" % fmt)
    return rng.choice(pool)


def write_result(out, result):
    with open(os.path.join(out, "net_guest_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    lines = ["NET GUEST: %s" % result["verdict"],
             "when: %s" % result["when"],
             "target: %s (%s)" % (result.get("target") or "-", "through a relay" if result.get("relay") else "direct"),
             "joined: %s%s" % ("yes" if result.get("joined") else "no",
                               ("  -  " + result["join_error"]) if result.get("join_error") else ""),
             "game: %s, turns %s, ended %s, %s s" % (result.get("format") or "-", result.get("turns"), result.get("ended"),
                                                   result.get("seconds")),
             "failing findings: %s" % (", ".join(result.get("fails") or []) or "none"),
             "warnings: %s" % (", ".join(result.get("warns") or []) or "none"),
             "version: %s" % result.get("version")]
    with open(os.path.join(out, "net_guest_result.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return "\n".join(lines)


def run(args, invite_text):
    out = args.out or os.path.join(BASE_DIR, "soak_runs", "net_guest_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    os.environ["MANTICORE_DATA_DIR"] = out              # the connection log, crash notes and engine logs stay in this folder
    from tools import online_soak, soak
    online_soak._load_modules()
    import bridge_rules
    import forge_client as fc
    import forge_net as fn
    result = {"verdict": "FAIL", "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "joined": False,
              "version": online_soak._version_line()}
    try:
        inv = fn.read_invite(invite_text)
    except fn.InviteError as e:
        result["join_error"] = "the invite code: %s" % e
        print(write_result(out, result))
        return 1, result
    ci_mask([inv.get("address"), inv.get("password"), args.address])
    if args.address:
        inv["address"] = args.address.strip()
        result["address_override"] = True
    if args.port:
        inv["port"] = int(args.port)
    result["target"] = "%s:%s" % (inv["address"], inv["port"])
    result["relay"] = bool(inv.get("room"))
    result["format"] = inv.get("format")
    rng = random.Random(args.seed)
    record = os.path.join(out, "guest.jsonl")
    dck_text = ""
    if not inv.get("draft"):
        deck = pick_deck(inv.get("format") or "commander", rng, args.deck)
        dck_path = online_soak.build_deck(deck, out, "guest")
        with open(dck_path, encoding="utf-8") as f:
            dck_text = f.read()
        result["deck"] = os.path.basename(deck)
    s = fc.NetSession(inv["address"], inv["port"], args.name, inv["password"], "guest", dck_text, fingerprint=inv["fingerprint"],
                      code="netguestbot", record_path=record, room=inv.get("room"), draft=bool(inv.get("draft")))
    t0 = time.time()
    try:
        s.start()
    except Exception as e:                                   # NetRefused (plain words in fn) or a socket error
        reason = getattr(e, "reason", None)                  # NetRefused: str(e) is already forge_net.join_message's words
        result["join_error"] = "%s%s: %s" % (type(e).__name__, (" " + reason) if reason else "", e)
        result["seconds"] = round(time.time() - t0, 1)
        print(write_result(out, result))
        return 1, result
    result["joined"] = True
    print("joined %s (%s); playing" % (result["target"], "relay" if result["relay"] else "direct"), flush=True)
    seat = online_soak.Seat("guest", s, rng.randrange(1 << 30))
    end = time.monotonic() + args.minutes * 60
    first_by = time.monotonic() + JOIN_SECONDS + FIRST_STATE_SECONDS
    ended, turns, conceded = "?", 0, False
    try:
        while True:
            seat.step()
            st = s.state or {}
            turns = max(turns, st.get("turn") or 0)
            if s.game_over:
                ended = "game_over"
                break
            if s.exited or getattr(s, "net_lost", None):
                ended = "connection_lost"
                break
            if getattr(s, "peer_left", None):
                ended = "host_left"
                break
            if not s.state and time.monotonic() > first_by:
                ended = "never_started"
                break
            if args.concede_turn and turns >= args.concede_turn and not conceded:
                s.concede()
                conceded = True
                result["conceded_at"] = turns
            if time.monotonic() > end:
                ended = "time_cap"
                break
            time.sleep(POLL_SLEEP)
    finally:
        try:
            s.close()
        except Exception:
            pass
    result.update(ended=ended, turns=turns, seconds=round(time.time() - t0, 1))
    findings = []
    try:
        msgs = bridge_rules.load_stream(record)
        findings = bridge_rules.check_stream(msgs, ())
    except Exception as e:
        findings = [{"rule": "record_unreadable", "severity": "warn", "detail": str(e)}]
    for leak in seat.hidden:
        findings.append({"rule": "online_hidden", "severity": "fail", "detail": leak})
    result["fails"] = sorted({f["rule"] for f in findings if f["severity"] == "fail"})
    result["warns"] = sorted({f["rule"] for f in findings if f["severity"] == "warn"})
    result["fail_details"] = [f.get("detail", "")[:300] for f in findings if f["severity"] == "fail"][:5]
    soak._compress_record(record)
    ok = ended == "game_over" and not result["fails"]
    result["verdict"] = "PASS" if ok else "FAIL"
    print(write_result(out, result))
    return (0 if ok else 1), result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Join a real host from an invite code and play one game (round RLY1).")
    ap.add_argument("--invite-file", default=None, help="a file holding the invite code (else MANTICORE_INVITE)")
    ap.add_argument("--deck", default=None, help="a deck file (default: a random sample deck of the invite's format)")
    ap.add_argument("--name", default="CloudBot")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--concede-turn", type=int, default=None, help="concede once the game reaches this turn")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--address", default=None, help="join this address instead of the invite's (same password and certificate)")
    ap.add_argument("--port", type=int, default=None, help="join this port instead of the invite's")
    args = ap.parse_args(argv)
    try:
        invite = read_invite_text(args.invite_file)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    code, _result = run(args, invite)
    return code


if __name__ == "__main__":
    sys.exit(main())
