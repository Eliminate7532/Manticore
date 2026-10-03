# SPDX-License-Identifier: GPL-3.0-or-later
"""
relay_agent.py - Round MP2d: the host's side of playing through a relay (relay/manticore_relay.py).

When the host's internet connection can't take incoming connections (CGNAT), the host's game connects OUT to a relay instead:
a control connection registers a "room", and for every guest the relay announces, a new data connection to the relay is
joined to a local connection to the host's own engine (NetHost, listening on 127.0.0.1 only). The bytes are copied both ways
unread: the game's TLS 1.3 runs inside, from the guest's table to NetHost, so the relay sees nothing but encrypted bytes.

    agent = RelayAgent("relay.example.com", 36900, local_port=36800).start()
    agent.status      # "connecting" / "ready" / "lost" (trying again) / "failed" (gave up; agent.error says why) / "closed"
    agent.room        # goes into the invite code with the relay's address and port
    agent.close()

The room's key proves to the relay that a reconnecting control connection is the same host; it never leaves this process and
the relay. Nothing is logged except counts and the relay's address.
"""
import json
import secrets
import socket
import string
import threading
import time

RELAY_V = 1
DEFAULT_RELAY_PORT = 36900
CONNECT_SECONDS = 10
PING_SECONDS = 20
RETRY_WAITS = (1, 2, 5, 10, 20)        # seconds between attempts to reach the relay again (the last one repeats)
FINAL = ("taken", "version", "bad", "full")
LINE_MAX = 4096
BUF = 64 * 1024
ROOM_ALPHABET = string.ascii_lowercase + string.digits


def make_room(rng=None):
    pick = (rng or secrets.SystemRandom()).choice
    return "".join(pick(ROOM_ALPHABET) for _ in range(10))


def send_line(sock, msg):
    sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))


def read_line(sock, limit=LINE_MAX):
    """One JSON line, read a byte at a time so nothing after it is taken from the socket (raw bytes follow "paired")."""
    buf = bytearray()
    while len(buf) < limit:
        b = sock.recv(1)
        if not b:
            return None
        if b == b"\n":
            try:
                m = json.loads(buf.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            return m if isinstance(m, dict) else None
        buf += b
    return None


def splice(a, b, on_end=None):
    """Copy bytes both ways between two sockets until either side ends, then close both."""
    done = threading.Event()

    def pipe(src, dst):
        try:
            while True:
                data = src.recv(BUF)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            if done.is_set():
                for s in (a, b):
                    try:
                        s.close()
                    except OSError:
                        pass
                if on_end:
                    on_end()
            done.set()

    for src, dst in ((a, b), (b, a)):
        threading.Thread(target=pipe, args=(src, dst), daemon=True, name="relay-pipe").start()


class RelayAgent:
    def __init__(self, relay_address, relay_port, local_port, room=None, key=None):
        self.relay = (relay_address, int(relay_port))
        self.local_port = int(local_port)
        self.room = room or make_room()
        self._key = key or secrets.token_urlsafe(24)   # never logged
        self.status = "connecting"
        self.error = None
        self.forwarded = 0                              # guest connections passed to the engine
        self.active = 0
        self._closing = False
        self._control = None
        self._lock = threading.Lock()

    def __repr__(self):
        return "RelayAgent(%s:%d, room %s, %s)" % (self.relay[0], self.relay[1], self.room, self.status)

    # ---- the control connection ----
    def start(self):
        threading.Thread(target=self._run, daemon=True, name="relay-control").start()
        return self

    def _run(self):
        attempt = 0
        while not self._closing:
            attempt += 1
            try:
                self._session()
                attempt = 0                             # it was up: start the waits again
            except _Refused as e:
                self.error = "the relay refused this game (%s)" % e.reason
                if e.reason in FINAL:
                    self.status = "failed"
                    return
            except (OSError, ValueError) as e:
                self.error = "couldn't reach the relay (%s)" % (e.__class__.__name__,)
            if self._closing:
                break
            self.status = "lost" if self.status in ("ready", "lost") else "connecting"
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS)) - 1] if attempt else RETRY_WAITS[0]
            end = time.monotonic() + wait
            while not self._closing and time.monotonic() < end:
                time.sleep(0.1)
        self.status = "closed"

    def _session(self):
        sock = socket.create_connection(self.relay, timeout=CONNECT_SECONDS)
        with self._lock:
            if self._closing:
                sock.close()
                return
            self._control = sock
        try:
            send_line(sock, {"c": "host", "v": RELAY_V, "room": self.room, "key": self._key})
            reply = read_line(sock)
            if reply is None:
                raise OSError("the relay closed the connection")
            if reply.get("t") == "refused":
                raise _Refused(reply.get("reason") or "unknown")
            if reply.get("t") != "hosting":
                raise ValueError("unexpected reply")
            self.status, self.error = "ready", None
            sock.settimeout(1.0)                        # wake up to ping; a socket file can't be read again after a timeout
            buf = bytearray()
            last_ping = time.monotonic()
            while not self._closing:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    chunk = None
                if chunk == b"":
                    raise OSError("the relay closed the connection")
                if chunk:
                    buf += chunk
                    if len(buf) > 64 * LINE_MAX:
                        raise ValueError("the relay sent too much")
                    while b"\n" in buf:
                        line, _sep, rest = bytes(buf).partition(b"\n")
                        buf = bytearray(rest)
                        try:
                            m = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if isinstance(m, dict) and m.get("t") == "incoming":
                            threading.Thread(target=self._forward, args=(str(m.get("id")),), daemon=True,
                                             name="relay-forward").start()
                if time.monotonic() - last_ping >= PING_SECONDS:
                    send_line(sock, {"c": "ping"})
                    last_ping = time.monotonic()
        finally:
            with self._lock:
                self._control = None
            try:
                sock.close()
            except OSError:
                pass

    # ---- one guest ----
    def _forward(self, gid):
        data = local = None
        try:
            data = socket.create_connection(self.relay, timeout=CONNECT_SECONDS)
            send_line(data, {"c": "accept", "v": RELAY_V, "room": self.room, "key": self._key, "id": gid})
            reply = read_line(data)
            if not reply or reply.get("t") != "paired":
                raise OSError("not paired")
            local = socket.create_connection(("127.0.0.1", self.local_port), timeout=CONNECT_SECONDS)
            data.settimeout(None)
            local.settimeout(None)
        except OSError:
            for s in (data, local):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
            return
        with self._lock:
            self.forwarded += 1
            self.active += 1

        def ended():
            with self._lock:
                self.active -= 1
        splice(data, local, ended)

    def close(self):
        self._closing = True
        with self._lock:
            sock = self._control
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


class _Refused(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
