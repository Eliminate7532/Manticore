# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/net_fake.py - Round MP1: a stand-in for the network host (java_bridge NetHost) on a real local socket, for the fast tests.

It speaks the handshake (one JSON line each way) over plain TCP: the TLS layer itself is tested live against the real NetHost
(tests/test_mp1.py LiveTests), because Python's standard library can't make a certificate. Tests point NetSession at it with
plain_connect (a stand-in for forge_net.connect_tls), never by changing the program's own code.

    host = FakeHost("welcome").start()        # or "refused:password", "refused:deck:Forge could not read the deck.",
                                              # "silent" (never answers), "close" (hangs up at once)
    ... NetSession("127.0.0.1", host.port, ...) with NetSession._connect = plain_connect ...
    host.hellos                                # what the guest sent
"""
import json
import socket
import threading

import forge_net


def plain_connect(address, port, expected_fp=None, timeout=10):
    """forge_net.connect_tls's stand-in: the same error handling, no TLS."""
    try:
        s = socket.create_connection((address, int(port)), timeout=timeout)
    except ConnectionRefusedError:
        raise forge_net.NetRefused("refused_connection") from None
    except socket.timeout:
        raise forge_net.NetRefused("no_answer") from None
    return s


class FakeHost:
    def __init__(self, mode="welcome", host_name="Karl", code="hostcode", after=()):
        self.mode = mode
        self.host_name, self.code = host_name, code
        self.after = list(after)              # messages sent after the welcome (e.g. ready, a state)
        self.hellos = []
        self.received = []                    # lines after the hello
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.conn = None
        self.done = threading.Event()

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def _serve(self):
        try:
            conn, _addr = self.server.accept()
        except OSError:
            return
        self.conn = conn
        try:
            if self.mode == "close":
                conn.close()
                return
            f = conn.makefile("rb")
            line = f.readline()
            if line:
                self.hellos.append(json.loads(line.decode("utf-8")))
            if self.mode == "silent":
                self.done.wait(30)
                return
            if self.mode.startswith("refused:"):
                parts = self.mode.split(":", 2)
                reply = {"t": "refused", "reason": parts[1]}
                if len(parts) > 2:
                    reply["text"] = parts[2]
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                conn.close()
                return
            conn.sendall((json.dumps({"t": "welcome", "v": 1, "host": self.host_name, "seat": 2, "code": self.code}) + "\n")
                         .encode("utf-8"))
            for m in self.after:
                conn.sendall((json.dumps(m) + "\n").encode("utf-8"))
            for raw in f:
                self.received.append(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError):
            pass

    def send(self, msg):
        self.conn.sendall((json.dumps(msg) + "\n").encode("utf-8"))

    def hang_up(self):
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()

    def stop(self):
        self.done.set()
        for s in (self.conn, self.server):
            try:
                if s:
                    s.close()
            except OSError:
                pass


class SeatHost:
    """Round MP2: a stand-in host that keeps the guest's seat across connections, like NetHost with a grace period.

    Each connection's first line is kept in `firsts`. A hello gets a welcome with `token` and `grace`; a rejoin with that token
    gets a welcome with "rejoined": true (any other token: refused "rejoin"); a watch gets a spectator welcome. After each
    welcome the `after` messages are sent. `lines` holds every later line from any connection; send() and hang_up() act on the
    newest connection; refuse_rejoins = "gone" makes rejoins fail as if the game had ended."""

    def __init__(self, token="t0k3n-abc", grace=30, after=(), host_name="Karl"):
        self.token, self.grace, self.after, self.host_name = token, grace, list(after), host_name
        self.firsts, self.lines, self.conns = [], [], []
        self.refuse_rejoins = None
        self.accepting = True
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def _accept(self):
        while True:
            try:
                conn, _addr = self.server.accept()
            except OSError:
                return
            if not self.accepting:
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            f = conn.makefile("rb")
            line = f.readline()
            if not line:
                conn.close()
                return
            first = json.loads(line.decode("utf-8"))
            self.firsts.append(first)
            c = first.get("c")
            if c == "rejoin" and (self.refuse_rejoins or first.get("token") != self.token):
                reason = self.refuse_rejoins or "rejoin"
                conn.sendall((json.dumps({"t": "refused", "reason": reason}) + "\n").encode("utf-8"))
                conn.close()
                return
            welcome = {"t": "welcome", "v": 2, "host": self.host_name, "seat": 2, "code": "hostcode"}
            if c == "watch":
                welcome.update(watching=True, guest="Sam")
            else:
                welcome.update(token=self.token, grace=self.grace)
                if c == "rejoin":
                    welcome["rejoined"] = True
            conn.sendall((json.dumps(welcome) + "\n").encode("utf-8"))
            with self.lock:
                self.conns.append(conn)
            for m in self.after:
                conn.sendall((json.dumps(m) + "\n").encode("utf-8"))
            for raw in f:
                self.lines.append(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError):
            pass

    def send(self, msg):
        with self.lock:
            conn = self.conns[-1]
        conn.sendall((json.dumps(msg) + "\n").encode("utf-8"))

    def hang_up(self):
        with self.lock:
            conn = self.conns[-1]
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()

    def stop(self):
        self.accepting = False
        for s in [self.server] + list(self.conns):
            try:
                s.close()
            except OSError:
                pass
