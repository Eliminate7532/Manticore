// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.net.Socket;
import java.util.function.BiConsumer;
import java.util.function.Consumer;

/**
 * Round MP2: one remote seat of an online game (the guest, or a spectator) that outlives its network connection.
 *
 * In MP1 the guest's BridgeGui wrote straight to the connection (RemoteWire), so a dropped connection was the end of the seat:
 * its questions failed and NetHost conceded for it. A SeatWire is what the BridgeGui talks to instead. It keeps the seat's
 * questions (Wire's pending list) and hands each line to whichever connection is current:
 *   - attach() puts a new connection in place (a reconnect drops any old one, which may be half-open and not know it yet);
 *   - while no connection is attached, lines are dropped - the next connection gets a fresh snapshot and every question still
 *     waiting (Wire.resendPending), which is all the table needs;
 *   - questions Forge asks meanwhile simply wait, so Forge's game thread waits for the player to come back, as it would for a
 *     slow player. NetHost gives up after its grace period and closes the seat, and the game ends as in MP1.
 * Each connection gets its own reader thread; replies on any of them complete this seat's questions.
 */
final class SeatWire extends Wire {
    final int seat;
    final String token;                     // the secret a reconnecting table proves itself with (never logged)
    volatile String name;
    private volatile RemoteWire conn;
    private volatile String lastDropReason;
    private volatile long lastHeard = System.currentTimeMillis();
    private final Object lock = new Object();
    // Round MP2c: what NetHost knows about this seat (a guest's; unused for a spectator)
    volatile BridgeGui gui;                 // set when the game starts
    volatile String deckPath;               // where this guest's deck was written
    volatile String code;                   // its copy's version code
    volatile boolean leaving;               // it said "leave": no grace period
    volatile boolean expired;               // its grace period ran out
    final java.util.concurrent.atomic.AtomicBoolean gone = new java.util.concurrent.atomic.AtomicBoolean(false);
    volatile java.util.concurrent.ScheduledFuture<?> graceTask;
    /** Round MP2e: held while NetHost announces this seat's connection dropped (detach, "peer_dropped", the grace timer) and
     *  while a reconnect announces it back (attach, "peer_back"), so the two never cross: an old connection ending just as
     *  the new one arrived could otherwise say "dropped" after "back", and the host's table kept a dropped-friend line for a
     *  friend who was playing. */
    final Object announce = new Object();

    SeatWire(int seat, String name, String token, int maxPerSecond) {
        super(null, null, maxPerSecond, seat == 0 ? "spectator" : "seat " + seat);
        this.seat = seat;
        this.name = name;
        this.token = token;
    }

    /** Round MP2b: while a saved game is played back, a guest who is already back hears nothing of it (the play-back answers
     *  its questions); sendNow() still reaches it. */
    volatile boolean muted = false;

    @Override
    protected void emit(String line) {
        RemoteWire c = conn;
        if (c != null && !muted) {
            c.write(line);
        }
    }

    void sendNow(JsonObject m) {
        RemoteWire c = conn;
        if (c != null) {
            c.write(m.toString());
        }
    }

    /** Puts a new connection in place and starts reading it. onEnd(this, connection) runs when that connection's reading ends
     *  (for any reason); NetHost then decides whether the seat is gone or only waiting for a reconnect. */
    void attach(Socket socket, LineReader reader, Consumer<JsonObject> handler, BiConsumer<SeatWire, RemoteWire> onEnd)
            throws IOException {
        RemoteWire c = new RemoteWire(socket, reader, maxPerSecond);
        c.label = seat == 0 ? "spectator " + name : "the guest";
        RemoteWire old;
        synchronized (lock) {
            old = conn;
            conn = c;
        }
        lastHeard = System.currentTimeMillis();
        if (old != null) {
            old.drop("replaced by a new connection from the same table");
        }
        Thread t = new Thread(() -> {
            c.readError = readFrom(reader, cmd -> {
                lastHeard = System.currentTimeMillis();
                handler.accept(cmd);
            });
            c.drop(c.readError != null ? "reading failed: " + c.readError : "the other side closed the connection");
            onEnd.accept(this, c);
        }, (seat == 0 ? "spectator" : "seat-" + seat) + "-reader");
        t.setDaemon(true);
        t.start();
    }

    /** The connection's reading ended. True when it was the current one: the seat now has no connection. */
    boolean detach(RemoteWire c) {
        synchronized (lock) {
            if (conn != c) {
                return false;
            }
            conn = null;
        }
        lastDropReason = c.dropReason();
        return true;
    }

    boolean attached() {
        RemoteWire c = conn;
        return c != null && !c.isDropped();
    }

    /** Drop the current connection (no answer for too long, or the seat is closing). Its reader then ends and onEnd runs. */
    void dropConnection(String reason) {
        RemoteWire c = conn;
        if (c != null) {
            c.drop(reason);
        }
    }

    long lastHeard() {
        return lastHeard;
    }

    String dropReason() {
        RemoteWire c = conn;
        String r = c != null ? c.dropReason() : null;
        return r != null ? r : lastDropReason;
    }

    void drain(long millis) {
        RemoteWire c = conn;
        if (c != null) {
            c.drain(millis);
        }
    }
}
