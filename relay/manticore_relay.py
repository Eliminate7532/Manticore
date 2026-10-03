# SPDX-License-Identifier: GPL-3.0-or-later
"""
relay/manticore_relay.py - Round MP2d: a meeting point for online games when the host can't accept incoming connections
(CGNAT, a router that won't forward a port) and Tailscale isn't wanted.

    python manticore_relay.py [--bind 0.0.0.0] [--port 36900] [--max-rooms 200] [--log FILE]

Nothing here is needed to play: it runs on a server somebody puts on the internet (a small VPS is plenty), and the host's
game connects OUT to it. NOT deployed anywhere yet - see relay/README.txt.

How it works (one JSON line each way, then the two connections are joined byte for byte):
  host's game, control connection:  {"c":"host","v":1,"room":"<room>","key":"<secret>"}  ->  {"t":"hosting","room"}
      then, for each guest:                                                                  <-  {"t":"incoming","id":"<n>"}
      and every 20 s:               {"c":"ping"}                                              ->  {"t":"pong"}
  host's game, one data connection per guest:  {"c":"accept","v":1,"room","key","id"}        ->  {"t":"paired"}
  guest's game:                     {"c":"join","v":1,"room":"<room>"}                        ->  {"t":"paired"}
  After "paired" the relay copies bytes both ways and never looks at them. The game's own TLS 1.3 runs INSIDE that stream, from
  the guest to the host's engine, and the guest checks the host's certificate fingerprint from the invite code, so the relay
  can't read or change anything: it sees encrypted bytes, their sizes and the two internet addresses. The game password is
  checked by the host's engine as before; the relay never sees it.
  Refusals: {"t":"refused","reason":"version"|"taken"|"full"|"no_room"|"busy"|"no_answer"|"bad"} and the connection closes.

Limits: --max-rooms rooms; 8 guests waiting per room; 32 connections per address; a guest waits at most 15 s for the host's
game to answer; a joined pair that sends nothing for 10 minutes is closed (a game sends a heartbeat every 5 s).
Only the Python standard library is used, so the file can be copied onto any server with Python 3.9+.
"""
import argparse
import asyncio
import json
import logging
import re
import secrets
import time

RELAY_V = 1
LINE_MAX = 4096
HELLO_SECONDS = 10
PAIR_SECONDS = 15
IDLE_SECONDS = 600
CONTROL_IDLE_SECONDS = 90           # the host's game pings every 20 s
PER_ADDRESS = 32
WAITING_PER_ROOM = 8
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
BUF = 64 * 1024

log = logging.getLogger("relay")


class Room:
    def __init__(self, name, key, control):
        self.name, self.key, self.control = name, key, control
        self.waiting = {}                 # id -> (guest reader, guest writer, future set when the host's data connection arrives)
        self.next_id = 1
        self.last_heard = time.monotonic()


class Relay:
    def __init__(self, max_rooms=200):
        self.max_rooms = max_rooms
        self.rooms = {}
        self.per_address = {}
        self.pairs = 0
        self.server = None

    # ---- plumbing -------------------------------------------------------------------------------------------------------
    @staticmethod
    async def read_line(reader, seconds):
        line = await asyncio.wait_for(reader.readline(), seconds)
        if not line or len(line) > LINE_MAX or not line.endswith(b"\n"):
            return None
        try:
            m = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return m if isinstance(m, dict) else None

    @staticmethod
    async def send(writer, msg):
        writer.write((json.dumps(msg) + "\n").encode("utf-8"))
        await writer.drain()

    async def refuse(self, writer, reason):
        try:
            await self.send(writer, {"t": "refused", "reason": reason})
        except (ConnectionError, OSError):
            pass
        writer.close()

    # ---- one connection -------------------------------------------------------------------------------------------------
    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("?", 0)
        addr = peer[0]
        if self.per_address.get(addr, 0) >= PER_ADDRESS:
            writer.close()
            return
        self.per_address[addr] = self.per_address.get(addr, 0) + 1
        try:
            try:
                m = await self.read_line(reader, HELLO_SECONDS)
            except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
                m = None
            if m is None:
                writer.close()
                return
            if m.get("v") != RELAY_V:
                await self.refuse(writer, "version")
                return
            c = m.get("c")
            if c == "host":
                await self.host(m, reader, writer)
            elif c == "accept":
                await self.accept(m, reader, writer)
            elif c == "join":
                await self.join(m, reader, writer)
            else:
                await self.refuse(writer, "bad")
        finally:
            self.per_address[addr] -= 1
            if self.per_address[addr] <= 0:
                del self.per_address[addr]

    async def host(self, m, reader, writer):
        name, key = str(m.get("room") or ""), str(m.get("key") or "")
        if not ROOM_RE.match(name) or not KEY_RE.match(key):
            await self.refuse(writer, "bad")
            return
        old = self.rooms.get(name)
        if old is not None:
            if not secrets.compare_digest(old.key, key):
                await self.refuse(writer, "taken")
                return
            old.control.close()                    # the same host coming back (its control connection dropped): take over
        elif len(self.rooms) >= self.max_rooms:
            await self.refuse(writer, "full")
            return
        room = Room(name, key, writer)
        if old is not None:
            room.waiting, room.next_id = old.waiting, old.next_id
        self.rooms[name] = room
        log.info("room %s opened", name)
        try:
            await self.send(writer, {"t": "hosting", "room": name})
            while True:
                try:
                    msg = await self.read_line(reader, CONTROL_IDLE_SECONDS)
                except asyncio.TimeoutError:
                    break
                if msg is None:
                    break
                room.last_heard = time.monotonic()
                if msg.get("c") == "ping":
                    await self.send(writer, {"t": "pong"})
        except (ConnectionError, OSError):
            pass
        finally:
            if self.rooms.get(name) is room:
                del self.rooms[name]
                log.info("room %s closed", name)
                for _gid, (_r, w, fut) in list(room.waiting.items()):
                    if not fut.done():
                        fut.cancel()
            writer.close()

    async def join(self, m, reader, writer):
        room = self.rooms.get(str(m.get("room") or ""))
        if room is None:
            await self.refuse(writer, "no_room")
            return
        if len(room.waiting) >= WAITING_PER_ROOM:
            await self.refuse(writer, "busy")
            return
        gid = str(room.next_id)
        room.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        room.waiting[gid] = (reader, writer, fut)
        try:
            await self.send(room.control, {"t": "incoming", "id": gid})
            host_reader, host_writer = await asyncio.wait_for(fut, PAIR_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError, OSError):
            room.waiting.pop(gid, None)
            await self.refuse(writer, "no_answer")
            return
        room.waiting.pop(gid, None)
        try:
            await self.send(writer, {"t": "paired"})
            await self.send(host_writer, {"t": "paired"})
        except (ConnectionError, OSError):
            writer.close()
            host_writer.close()
            return
        self.pairs += 1
        log.info("room %s: guest %s paired (%d pairs so far)", room.name, gid, self.pairs)
        await self.splice(reader, writer, host_reader, host_writer)

    async def accept(self, m, reader, writer):
        room = self.rooms.get(str(m.get("room") or ""))
        if room is None or not secrets.compare_digest(room.key, str(m.get("key") or "")):
            await self.refuse(writer, "no_room")
            return
        entry = room.waiting.get(str(m.get("id") or ""))
        if entry is None or entry[2].done():
            await self.refuse(writer, "no_answer")
            return
        entry[2].set_result((reader, writer))
        # the guest's coroutine now owns this connection (splice); keep this one alive until it is closed
        await self.wait_closed(writer)

    @staticmethod
    async def wait_closed(writer):
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def splice(self, r1, w1, r2, w2):
        last = [time.monotonic()]

        async def pipe(src, dst):
            try:
                while True:
                    data = await src.read(BUF)
                    if not data:
                        break
                    last[0] = time.monotonic()
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.write_eof()
                except (OSError, RuntimeError, AttributeError):
                    pass

        async def idle():
            while True:
                await asyncio.sleep(min(30, IDLE_SECONDS))
                if time.monotonic() - last[0] > IDLE_SECONDS:
                    return

        tasks = [asyncio.ensure_future(pipe(r1, w2)), asyncio.ensure_future(pipe(r2, w1)), asyncio.ensure_future(idle())]
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if tasks[2] not in done:                       # one side ended: let the other finish what's in flight
                await asyncio.wait(tasks[:2], timeout=5)
        finally:
            for t in tasks:
                t.cancel()
            for w in (w1, w2):
                w.close()

    # ---- running --------------------------------------------------------------------------------------------------------
    async def start(self, bind="0.0.0.0", port=36900):
        self.server = await asyncio.start_server(self.handle, bind, port)
        return self.server.sockets[0].getsockname()[1]

    async def serve_forever(self):
        async with self.server:
            await self.server.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Manticore's meeting point for online games (round MP2d).")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=36900)
    ap.add_argument("--max-rooms", type=int, default=200)
    ap.add_argument("--log", default=None, help="a log file (default: the terminal)")
    args = ap.parse_args(argv)
    logging.basicConfig(filename=args.log, level=logging.INFO, format="%(asctime)s %(message)s")
    relay = Relay(args.max_rooms)

    async def run():
        port = await relay.start(args.bind, args.port)
        log.info("relay listening on %s:%d", args.bind, port)
        await relay.serve_forever()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
