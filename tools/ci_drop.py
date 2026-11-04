# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/ci_drop.py - Round RLY1: hand an invite code to a GitHub Actions runner in a PUBLIC repository, and get its results back,
without the code (the host's address and the game's password) or the results (connection logs name the address) ever being
readable by anyone else. Everything goes through the branch itself, encrypted:

  1. the runner makes a key pair for this run only (the private half never leaves the runner) and pushes the public half:
         ci_drop/<run>/runner.pub
  2. the operator (Claude, or Karl) encrypts an invite to it and pushes it:
         ci_drop/<run>/invite.<n>.enc         (python tools/ci_drop.py send-invite ...)
  3. the runner decrypts it, masks the address and password in its log, plays (tools/net_guest_bot.py), then encrypts the whole
     output folder to the operator's public key (ci_drop/operator.pub) and pushes:
         ci_drop/<run>/result.<n>.enc         (only the operator's private key opens it: open-results)
         ci_drop/<run>/status.<n>.txt         (plain: PASS/FAIL, joined, ended, turns - the address and password scrubbed out)
  4. it waits for invite.<n+1> until its time is up, so a second try needs no new run.

Encryption: openssl (on every GitHub runner and in Git for Windows): a random 256-bit key, AES-256-CBC with PBKDF2, the key
wrapped with RSA-OAEP-SHA256; the plaintext's SHA-256 travels inside the envelope and is checked after decryption, so a damaged
or tampered file is refused, never half-used.

Subcommands (run with -h for each):
  operator-key   make the operator's key pair (private key: keep it out of every repository)
  runner-key     make this run's key pair (on the runner)
  send-invite    encrypt an invite code (and the bot's options) to a run's public key
  wait-invite    on the runner: poll the branch for invite.<n>.enc, decrypt, mask, write the invite and options
  runner-loop    on the runner: all of the above, up to --tries invites, each sealed and pushed back
  seal           on the runner: encrypt a folder to the operator's key and write the scrubbed status
  open-results   decrypt result.<n>.enc into a folder
"""
import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

RSA_BITS = 3072
PBKDF2_ITER = 200000
# options the operator may pass to tools/net_guest_bot.py inside the encrypted envelope (nothing else is ever run)
BOT_OPTIONS = {"minutes": "--minutes", "concede_turn": "--concede-turn", "deck": "--deck", "name": "--name", "seed": "--seed",
               "address": "--address", "port": "--port"}


class DropError(Exception):
    pass


def _openssl(args, data=None):
    p = subprocess.run(["openssl"] + list(args), input=data, capture_output=True)
    if p.returncode != 0:
        raise DropError("openssl %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()[:300]))
    return p.stdout


def make_key_pair(priv_path, pub_path, bits=RSA_BITS):
    os.makedirs(os.path.dirname(os.path.abspath(priv_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(pub_path)), exist_ok=True)
    old = os.umask(0o077)
    try:
        _openssl(["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:%d" % bits, "-out", priv_path])
    finally:
        os.umask(old)
    _openssl(["pkey", "-in", priv_path, "-pubout", "-out", pub_path])
    return pub_path


def seal_bytes(data, pub_path):
    """bytes -> one JSON envelope (bytes) only the holder of pub_path's private key can open."""
    with tempfile.TemporaryDirectory() as d:
        keyfile = os.path.join(d, "k")
        with open(keyfile, "w") as f:
            f.write(os.urandom(32).hex())
        os.chmod(keyfile, 0o600)
        body = _openssl(["enc", "-aes-256-cbc", "-pbkdf2", "-iter", str(PBKDF2_ITER), "-salt", "-pass", "file:" + keyfile],
                        data=data)
        wrapped = _openssl(["pkeyutl", "-encrypt", "-pubin", "-inkey", pub_path, "-pkeyopt", "rsa_padding_mode:oaep",
                            "-pkeyopt", "rsa_oaep_md:sha256", "-in", keyfile])
    env = {"v": 1, "sha256": hashlib.sha256(data).hexdigest(), "key": base64.b64encode(wrapped).decode("ascii"),
           "body": base64.b64encode(body).decode("ascii")}
    return json.dumps(env).encode("ascii")


def open_bytes(envelope, priv_path):
    try:
        env = json.loads(envelope.decode("ascii"))
        wrapped = base64.b64decode(env["key"])
        body = base64.b64decode(env["body"])
        want = env["sha256"]
    except (ValueError, KeyError, UnicodeDecodeError) as e:
        raise DropError("not an envelope: %s" % e)
    with tempfile.TemporaryDirectory() as d:
        wfile = os.path.join(d, "w")
        with open(wfile, "wb") as f:
            f.write(wrapped)
        key = _openssl(["pkeyutl", "-decrypt", "-inkey", priv_path, "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt",
                        "rsa_oaep_md:sha256", "-in", wfile])
        keyfile = os.path.join(d, "k")
        with open(keyfile, "wb") as f:
            f.write(key)
        data = _openssl(["enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", str(PBKDF2_ITER), "-pass", "file:" + keyfile],
                        data=body)
    if hashlib.sha256(data).hexdigest() != want:
        raise DropError("the decrypted data does not match its checksum (damaged or changed)")
    return data


def scrub(text, secrets):
    for s in sorted({str(x) for x in secrets if x and len(str(x)) >= 4}, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def bot_args(options):
    """The envelope's options -> net_guest_bot arguments; anything not in BOT_OPTIONS is refused."""
    out = []
    for k, v in sorted((options or {}).items()):
        if k not in BOT_OPTIONS:
            raise DropError("option %r is not allowed" % k)
        out += [BOT_OPTIONS[k], str(v)]
    return out


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def remote_file(cwd, remote, branch, path):
    """The bytes of path on the remote branch as it is now (git fetch + git show), or None. Leaves the working tree alone."""
    if _git(["fetch", "--quiet", remote, branch], cwd).returncode != 0:
        return None
    p = subprocess.run(["git", "show", "FETCH_HEAD:" + path.replace(os.sep, "/")], cwd=cwd, capture_output=True)
    return p.stdout if p.returncode == 0 else None


def publish(cwd, remote, branch, files, message, tries=6, sleep=time.sleep):
    """Commit files ({path: bytes}) on top of the remote branch as it is now and push; retried when someone pushed between."""
    for i in range(tries):
        if _git(["fetch", "--quiet", remote, branch], cwd).returncode == 0:
            _git(["reset", "--quiet", "--hard", "FETCH_HEAD"], cwd)
        for rel, data in files.items():
            full = os.path.join(cwd, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
        a = _git(["add", "--"] + list(files), cwd)
        if a.returncode != 0:
            raise DropError("git add refused %s: %s" % (", ".join(files), a.stderr.strip()[:300]))
        c = _git(["commit", "--quiet", "-m", message], cwd)
        if c.returncode != 0 and "nothing to commit" in (c.stdout + c.stderr):
            return True
        if _git(["push", "--quiet", remote, "HEAD:" + branch], cwd).returncode == 0:
            return True
        sleep(2 + i)
    return False


def take_invite(envelope, priv, out_invite, out_args):
    """Decrypt an invite envelope, mask its secrets in a runner's log, write the invite and the bot's arguments (0600)."""
    msg = json.loads(open_bytes(envelope, priv).decode("utf-8"))
    invite = (msg.get("invite") or "").strip()
    from tools import net_guest_bot
    secrets = [(msg.get("options") or {}).get("address")]
    try:
        import forge_net
        inv = forge_net.read_invite(invite)
        secrets += [inv.get("address"), inv.get("password")]
    except Exception:
        pass
    net_guest_bot.ci_mask(secrets)
    args = bot_args(msg.get("options"))
    old = os.umask(0o077)
    try:
        with open(out_invite, "w", encoding="utf-8") as f:
            f.write(invite + "\n")
        with open(out_args, "w", encoding="utf-8") as f:
            json.dump({"args": args, "scrub": [s for s in secrets if s]}, f)
    finally:
        os.umask(old)
    return args, [s for s in secrets if s]


def wait_invite(drop_dir, priv, n, out_invite, out_args, minutes, remote="origin", branch=None, cwd=None, poll=10.0,
                sleep=time.sleep, now=time.monotonic):
    """Poll for <drop_dir>/invite.<n>.enc - on the remote branch when one is given, else on disk - and take it. True when found."""
    cwd = cwd or os.getcwd()
    rel = os.path.join(drop_dir, "invite.%d.enc" % n)
    end = now() + minutes * 60
    while True:
        if branch:
            data = remote_file(cwd, remote, branch, rel)
        else:
            full = os.path.join(cwd, rel)
            data = open(full, "rb").read() if os.path.exists(full) else None
        if data:
            take_invite(data, priv, out_invite, out_args)
            return True
        if now() >= end:
            return False
        sleep(poll)


def runner_loop(run_id, branch, tries=6, wait_minutes=40.0, remote="origin", cwd=None, temp=None, poll=10.0,
                bot=None, log=print):
    """On the runner: publish this run's key, then up to `tries` times wait for invite.<n>.enc, play, publish the sealed results."""
    cwd = cwd or os.getcwd()
    temp = temp or os.environ.get("RUNNER_TEMP") or tempfile.mkdtemp()
    drop = os.path.join("ci_drop", str(run_id))
    priv = os.path.join(temp, "runner_%s.pem" % run_id)
    pub = os.path.join(temp, "runner_%s.pub" % run_id)
    make_key_pair(priv, pub)
    operator_pub = os.path.join(temp, "operator_pub.pem")
    data = remote_file(cwd, remote, branch, "ci_drop/operator.pub")
    if not data:
        raise DropError("ci_drop/operator.pub is not on %s: the results would have no one to go to" % branch)
    with open(operator_pub, "wb") as f:
        f.write(data)
    with open(pub, "rb") as f:
        if not publish(cwd, remote, branch, {os.path.join(drop, "runner.pub"): f.read()},
                       "ci_drop: run %s waits for an invite" % run_id):
            raise DropError("could not push this run's public key")
    log("run %s: public key pushed; waiting for %s/invite.1.enc" % (run_id, drop))
    played = 0
    for n in range(1, tries + 1):
        inv, argf = os.path.join(temp, "invite.txt"), os.path.join(temp, "args.json")
        if not wait_invite(drop, priv, n, inv, argf, wait_minutes, remote, branch, cwd, poll):
            log("no invite %d within %s min; stopping" % (n, wait_minutes))
            publish(cwd, remote, branch, {os.path.join(drop, "status.%d.txt" % n):
                                          ("try %d: no invite came within %s min\n" % (n, wait_minutes)).encode()},
                    "ci_drop: run %s gave up waiting" % run_id)
            break
        with open(argf, encoding="utf-8") as f:
            a = json.load(f)
        out = os.path.join(temp, "out_%d" % n)
        log("invite %d received; joining" % n)
        cmd = [sys.executable, os.path.join(BASE_DIR, "tools", "net_guest_bot.py"), "--invite-file", inv, "--out", out] + a["args"]
        (bot or subprocess.call)(cmd)
        try:
            os.remove(inv)
        except OSError:
            pass
        os.makedirs(out, exist_ok=True)
        enc = os.path.join(temp, "result.%d.enc" % n)
        seal_folder(out, operator_pub, enc)
        text = status_text(os.path.join(out, "net_guest_result.json"), a.get("scrub") or [], n, run_id)
        log(text.rstrip())
        with open(enc, "rb") as f:
            files = {os.path.join(drop, "result.%d.enc" % n): f.read(),
                     os.path.join(drop, "status.%d.txt" % n): text.encode("utf-8")}
        publish(cwd, remote, branch, files, "ci_drop: run %s try %d" % (run_id, n))
        played += 1
    return played


def seal_folder(src, operator_pub, dest_enc):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(src, arcname=os.path.basename(os.path.normpath(src)) or "out")
    with open(dest_enc, "wb") as f:
        f.write(seal_bytes(buf.getvalue(), operator_pub))


def status_text(result_json, secrets, n, run_id=None):
    try:
        with open(result_json, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {"verdict": "FAIL", "join_error": "no result file (the bot did not finish)"}
    lines = ["try %d%s: %s" % (n, (" of run %s" % run_id) if run_id else "", r.get("verdict")),
             "relay: %s" % ("yes" if r.get("relay") else "no"),
             "joined: %s" % ("yes" if r.get("joined") else "no"),
             "join_error: %s" % (r.get("join_error") or "-"),
             "ended: %s, turns %s, %s s" % (r.get("ended"), r.get("turns"), r.get("seconds")),
             "fails: %s" % (", ".join(r.get("fails") or []) or "none"),
             "warns: %s" % (", ".join(r.get("warns") or []) or "none"),
             "version: %s" % r.get("version")]
    return scrub("\n".join(lines) + "\n", secrets)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Encrypted invite and result drop for a public repository's runner (round RLY1).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("operator-key")
    a.add_argument("--priv", required=True)
    a.add_argument("--pub", required=True)
    a = sub.add_parser("runner-key")
    a.add_argument("--priv", required=True)
    a.add_argument("--pub", required=True)
    a = sub.add_parser("send-invite")
    a.add_argument("--pub", required=True, help="the run's runner.pub")
    a.add_argument("--invite-file", default=None, help="else MANTICORE_INVITE")
    a.add_argument("--options", default="{}", help='JSON, e.g. {"minutes": 20, "address": "192.168.0.161"}')
    a.add_argument("--out", required=True, help="ci_drop/<run>/invite.<n>.enc")
    a = sub.add_parser("wait-invite")
    a.add_argument("--dir", required=True)
    a.add_argument("--priv", required=True)
    a.add_argument("--n", type=int, default=1)
    a.add_argument("--out-invite", required=True)
    a.add_argument("--out-args", required=True)
    a.add_argument("--minutes", type=float, default=30)
    a.add_argument("--remote", default="origin")
    a.add_argument("--branch", default=None)
    a = sub.add_parser("runner-loop")
    a.add_argument("--run-id", required=True)
    a.add_argument("--branch", required=True)
    a.add_argument("--tries", type=int, default=6)
    a.add_argument("--wait-minutes", type=float, default=40)
    a.add_argument("--remote", default="origin")
    a = sub.add_parser("seal")
    a.add_argument("--src", required=True)
    a.add_argument("--operator-pub", required=True)
    a.add_argument("--out", required=True, help="ci_drop/<run>/result.<n>.enc")
    a.add_argument("--status", required=True, help="ci_drop/<run>/status.<n>.txt")
    a.add_argument("--args-file", default=None, help="wait-invite's --out-args (the values to scrub)")
    a.add_argument("--n", type=int, default=1)
    a.add_argument("--run-id", default=None)
    a = sub.add_parser("open-results")
    a.add_argument("--priv", required=True)
    a.add_argument("--file", required=True)
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd in ("operator-key", "runner-key"):
            make_key_pair(args.priv, args.pub)
            print("public key: %s" % args.pub)
        elif args.cmd == "send-invite":
            if args.invite_file:
                with open(args.invite_file, encoding="utf-8") as f:
                    invite = f.read().strip()
            else:
                invite = (os.environ.get("MANTICORE_INVITE") or "").strip()
            if not invite:
                raise DropError("no invite code")
            options = json.loads(args.options)
            bot_args(options)
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "wb") as f:
                f.write(seal_bytes(json.dumps({"invite": invite, "options": options}).encode("utf-8"), args.pub))
            print("wrote %s" % args.out)
        elif args.cmd == "wait-invite":
            ok = wait_invite(args.dir, args.priv, args.n, args.out_invite, args.out_args, args.minutes, args.remote, args.branch)
            print("invite %d %s" % (args.n, "received" if ok else "never came"))
            return 0 if ok else 2
        elif args.cmd == "runner-loop":
            played = runner_loop(args.run_id, args.branch, args.tries, args.wait_minutes, args.remote)
            print("tries played: %d" % played)
            return 0 if played else 2
        elif args.cmd == "seal":
            secrets = []
            if args.args_file and os.path.exists(args.args_file):
                with open(args.args_file, encoding="utf-8") as f:
                    secrets = json.load(f).get("scrub") or []
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            seal_folder(args.src, args.operator_pub, args.out)
            text = status_text(os.path.join(args.src, "net_guest_result.json"), secrets, args.n, args.run_id)
            with open(args.status, "w", encoding="utf-8") as f:
                f.write(text)
            print(text, end="")
        elif args.cmd == "open-results":
            with open(args.file, "rb") as f:
                data = open_bytes(f.read(), args.priv)
            os.makedirs(args.out, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(args.out, filter="data")
            print("opened into %s" % args.out)
    except (DropError, OSError, ValueError) as e:
        print("ci_drop: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
