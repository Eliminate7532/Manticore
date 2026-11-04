# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/relay_probe.py - Round RLY1: is a Manticore relay (relay/manticore_relay.py) up and pairing, as seen from outside?

    python tools/relay_probe.py ADDRESS:PORT [--timeout 10]

Five checks, each one line, through the relay's own protocol (no game, no Forge):
  1. tcp      - a TCP connection opens (the cloud firewall and the server's own firewall let it in)
  2. host     - {"c":"host"} for a random room is answered {"t":"hosting"} (the relay program is running)
  3. no_room  - joining a room nobody hosts is refused with reason no_room
  4. pair     - a guest joins the room, the relay announces it to the host ("incoming"), the host's data connection
                accepts it, and both sides are told "paired"
  5. bytes    - 64 KB each way through the pair arrive unchanged (the relay copies bytes both ways)
Exit code 0 when all pass, 1 otherwise. Prints no secret: the room and key are random and thrown away.
"""
import argparse
import json
import os
import secrets
import socket
import sys
import time


class Line:
    def __init__(self, sock):
        self.sock, self.buf = sock, b""

    def send(self, msg):
        self.sock.sendall((json.dumps(msg) + "\n").encode())

    def read(self):
        while b"\n" not in self.buf:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("the relay closed the connection")
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode())


def connect(host, port, timeout):
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    return s


def recv_exact(sock, n, already=b""):
    out = bytearray(already)
    while len(out) < n:
        data = sock.recv(min(65536, n - len(out)))
        if not data:
            raise ConnectionError("closed after %d of %d bytes" % (len(out), n))
        out += data
    return bytes(out)


def probe(host, port, timeout=10.0, say=print):
    room = "probe-" + secrets.token_hex(6)
    key = secrets.token_urlsafe(24)
    results = {}
    socks = []
    try:
        t0 = time.monotonic()
        try:
            ctl_sock = connect(host, port, timeout)
        except OSError as e:
            say("tcp      FAILED: %s" % (type(e).__name__ if not isinstance(e, socket.timeout) else "no answer within %g s" % timeout))
            return False
        socks.append(ctl_sock)
        say("tcp      OK  %d ms" % ((time.monotonic() - t0) * 1000))
        ctl = Line(ctl_sock)
        ctl.send({"c": "host", "v": 1, "room": room, "key": key})
        m = ctl.read()
        if m.get("t") != "hosting":
            say("host     FAILED: answered %s" % m)
            return False
        say("host     OK  the relay opened a room")
        nr = Line(connect(host, port, timeout))
        socks.append(nr.sock)
        nr.send({"c": "join", "v": 1, "room": "probe-" + secrets.token_hex(6)})
        m = nr.read()
        if m.get("t") != "refused" or m.get("reason") != "no_room":
            say("no_room  FAILED: answered %s" % m)
            return False
        say("no_room  OK  an unknown room is refused")
        guest = Line(connect(host, port, timeout))
        socks.append(guest.sock)
        guest.send({"c": "join", "v": 1, "room": room})
        m = ctl.read()
        if m.get("t") != "incoming":
            say("pair     FAILED: host was told %s" % m)
            return False
        data_conn = Line(connect(host, port, timeout))
        socks.append(data_conn.sock)
        data_conn.send({"c": "accept", "v": 1, "room": room, "key": key, "id": m.get("id")})
        a, b = data_conn.read(), guest.read()
        if a.get("t") != "paired" or b.get("t") != "paired":
            say("pair     FAILED: host side %s, guest side %s" % (a, b))
            return False
        say("pair     OK  guest and host paired through the relay")
        up, down = os.urandom(65536), os.urandom(65536)
        t1 = time.monotonic()
        guest.sock.sendall(up)
        got_up = recv_exact(data_conn.sock, len(up), data_conn.buf)
        data_conn.sock.sendall(down)
        got_down = recv_exact(guest.sock, len(down), guest.buf)
        if got_up != up or got_down != down:
            say("bytes    FAILED: the bytes changed on the way")
            return False
        say("bytes    OK  64 KB each way unchanged, %d ms" % ((time.monotonic() - t1) * 1000))
        return True
    except (OSError, ConnectionError, ValueError) as e:
        say("FAILED during the checks: %s: %s" % (type(e).__name__, e))
        return False
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a Manticore relay from outside.")
    ap.add_argument("address", help="HOST:PORT of the relay")
    ap.add_argument("--timeout", type=float, default=10.0)
    a = ap.parse_args(argv)
    host, _, port = a.address.rpartition(":")
    ok = probe(host, int(port), a.timeout)
    print("RELAY PROBE: %s" % ("ALL PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
