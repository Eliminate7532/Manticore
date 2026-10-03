// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Round MP1: the guest's seat in an online game, over its TLS connection.
 *
 * Everything sent to the guest goes into a queue that one writer thread empties. Writing straight to the socket (as the
 * host's own wire writes to stdout) would let a guest whose connection stalls - a frozen PC, a dead Wi-Fi link that hasn't
 * timed out yet - block whichever thread was sending: the snapshot flusher, or Forge's own game thread through the event
 * forwarder, and the host's game would freeze with it. Here a send never waits. If one write takes longer than STALL_SECONDS,
 * or the queue grows past its limits, the connection is closed, which ends the guest's read loop and so the online game
 * (NetHost.guestLeft), exactly as if the guest had closed their program.
 */
final class RemoteWire extends Wire {
    static final int MAX_QUEUED = 5000;                    // lines; a snapshot is ~8 KB, an event line ~150 bytes
    static final long MAX_QUEUED_BYTES = 32L << 20;
    static int stallSeconds = 30;

    private final Socket socket;
    private final OutputStream raw;
    private final LinkedBlockingQueue<byte[]> queue = new LinkedBlockingQueue<>();
    private final AtomicLong queuedBytes = new AtomicLong();
    private final AtomicBoolean dropped = new AtomicBoolean(false);
    private volatile long writingSince = 0;
    private volatile String dropReason = null;
    volatile String label;                                 // round MP2: "the guest" / "spectator Sam" in log lines
    volatile String readError;                             // round MP2: why reading this connection stopped (null: it closed)

    RemoteWire(Socket socket, LineReader reader, int maxPerSecond) throws IOException {
        super(null, reader, maxPerSecond, "guest");
        this.socket = socket;
        this.raw = socket.getOutputStream();
        Thread writer = new Thread(this::writeLoop, "guest-writer");
        writer.setDaemon(true);
        writer.start();
        Thread watch = new Thread(this::watchLoop, "guest-write-watch");
        watch.setDaemon(true);
        watch.start();
    }

    @Override
    protected void emit(String line) {
        if (dropped.get()) {
            return;
        }
        byte[] b = (line + "\n").getBytes(StandardCharsets.UTF_8);
        if (queue.size() >= MAX_QUEUED || queuedBytes.get() + b.length > MAX_QUEUED_BYTES) {
            drop("the guest's connection is not taking data (" + queue.size() + " messages waiting)");
            return;
        }
        queuedBytes.addAndGet(b.length);
        queue.add(b);
    }

    private void writeLoop() {
        try {
            while (!dropped.get()) {
                byte[] b = queue.poll(500, TimeUnit.MILLISECONDS);
                if (b == null) {
                    continue;
                }
                writingSince = System.currentTimeMillis();
                raw.write(b);
                // flush only when nothing else is waiting: many small lines in one go become fewer TLS records
                if (queue.isEmpty()) {
                    raw.flush();
                }
                writingSince = 0;
                queuedBytes.addAndGet(-b.length);
            }
        } catch (IOException e) {
            drop("writing to the guest failed: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void watchLoop() {
        while (!dropped.get()) {
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
                return;
            }
            long since = writingSince;
            if (since != 0 && System.currentTimeMillis() - since > stallSeconds * 1000L) {
                drop("one write to the guest took over " + stallSeconds + " s");
            }
        }
    }

    /** Give up on the connection: closing the socket ends the guest's read loop, and NetHost ends the game. */
    void drop(String reason) {
        if (!dropped.compareAndSet(false, true)) {
            return;
        }
        dropReason = reason;
        System.err.println("nethost: dropping " + (label != null ? label : "the guest") + "'s connection: " + reason);
        try {
            socket.close();
        } catch (IOException ignored) {
        }
    }

    String dropReason() {
        String d = dropReason;
        if (d == null && readError != null) {
            return "reading failed: " + readError;
        }
        return d;
    }

    boolean isDropped() {
        return dropped.get();
    }

    /** Round MP2: write one line for a SeatWire, which owns the seat's questions and reads the connection itself. */
    void write(String line) {
        emit(line);
    }

    /** Waits (at most `millis`) until everything queued has been written: used before the host's engine exits, so the guest
     *  still receives its last message ("the host left"). */
    void drain(long millis) {
        long end = System.currentTimeMillis() + millis;
        while (!dropped.get() && (!queue.isEmpty() || writingSince != 0) && System.currentTimeMillis() < end) {
            try {
                Thread.sleep(20);
            } catch (InterruptedException e) {
                return;
            }
        }
        try {
            raw.flush();
        } catch (IOException ignored) {
        }
    }
}
