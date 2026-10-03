package forge.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

/**
 * JSON-lines protocol over the process's real stdout/stdin. Forge itself prints a lot to System.out,
 * so Main redirects System.out to System.err and hands the original stream to this class.
 *
 * Round MP1: a wire can also read from a LineReader (the guest's network connection in an online game), with a limit on the
 * length of one line and on how many commands a second are taken (see readLoop). Writing goes through emit(), which
 * RemoteWire overrides so a guest whose connection stalls can never block Forge's game thread.
 */
public class Wire {
    private final PrintStream out;
    private final LineReader reader;                // null: read System.in, as before round MP1
    final int maxPerSecond;                         // 0: no limit
    final String who;                               // "host" / "guest" in log lines
    /** A question waiting for the client's reply. Round MP2: the question itself is kept too, so it can be asked again on a new
     *  connection when an online guest reconnects (SeatWire, NetHost). */
    private static final class Pending {
        final JsonObject msg;
        final CompletableFuture<JsonElement> future = new CompletableFuture<>();

        Pending(JsonObject msg) {
            this.msg = msg;
        }
    }

    private final Map<Integer, Pending> pending = new ConcurrentHashMap<>();
    private final AtomicInteger nextId = new AtomicInteger(1);
    private volatile boolean closed = false;
    /** Round MP2b: told about every reply that answers a waiting question (NetHost's journal of an online game). */
    volatile Consumer<JsonObject> replyListener;

    public Wire(PrintStream out) {
        this(out, null, 0, "host");
    }

    /** A wire over the guest's connection: lines longer than the reader's limit are dropped, and at most maxPerSecond lines a
     *  second are taken (the rest are dropped and logged once a second). Real play is far below the limit: a person clicks a
     *  few times a second, and even the test bots' stale clicks stay under it. */
    public Wire(PrintStream out, LineReader reader, int maxPerSecond, String who) {
        this.out = out;
        this.reader = reader;
        this.maxPerSecond = maxPerSecond;
        this.who = who;
    }

    public synchronized void send(JsonObject msg) {
        if (closed) {
            return;
        }
        emit(msg.toString());
    }

    /** Writes one line. RemoteWire queues it instead. */
    protected void emit(String line) {
        out.println(line);
        out.flush();
    }

    /** Ask the client something and block (on the game thread) until it answers. Returns the reply's "value". */
    public JsonElement request(JsonObject msg) {
        int id = nextId.getAndIncrement();
        msg.addProperty("t", "request");
        msg.addProperty("id", id);
        Pending p = new Pending(msg);
        CompletableFuture<JsonElement> f = p.future;
        pending.put(id, p);
        if (closed) {                                   // round MP1: the guest left before this question was asked
            f.completeExceptionally(new IOException("closed"));
        }
        send(msg);
        try {
            return f.get();
        } catch (Exception e) {
            throw new RuntimeException("client went away while waiting for a reply", e);
        } finally {
            pending.remove(id);
        }
    }

    /** Read commands until the input closes. Each parsed command goes to the handler; replies complete pending requests. */
    public void readLoop(Consumer<JsonObject> handler) {
        if (reader != null) {
            readFrom(reader, handler);
            close();
            return;
        }
        try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = in.readLine()) != null) {
                take(line, handler);
            }
        } catch (IOException e) {
            // fall through: treated as end of input
        }
        close();
    }

    /** Read one connection's lines until it ends, with the length and rate limits (see the constructor). Round MP2: a SeatWire
     *  reads each new connection of the same seat this way, so replies to its questions reach its own pending list. Does not
     *  close the wire. Returns null when the input simply ended, else what went wrong (round MP2: said in the log). */
    String readFrom(LineReader from, Consumer<JsonObject> handler) {
        long second = 0;
        int count = 0, dropped = 0, tooLong = 0;
        try {
            String line;
            while ((line = from.readLine()) != null) {
                if (line == LineReader.TOO_LONG) {
                    tooLong++;
                    System.err.println("bridge: " + who + ": a line over the length limit was dropped (" + tooLong + " so far)");
                    continue;
                }
                long now = System.currentTimeMillis() / 1000;
                if (now != second) {
                    if (dropped > 0) {
                        System.err.println("bridge: " + who + ": " + dropped + " commands over the limit of " + maxPerSecond
                                + " a second were dropped");
                    }
                    second = now;
                    count = 0;
                    dropped = 0;
                }
                if (maxPerSecond > 0 && ++count > maxPerSecond) {
                    dropped++;
                    continue;
                }
                take(line, handler);
            }
        } catch (IOException e) {
            // the connection went away: treated as end of input
            return e.toString();
        }
        return null;
    }

    private void take(String line, Consumer<JsonObject> handler) {
        line = line.trim();
        if (line.isEmpty()) {
            return;
        }
        JsonObject cmd;
        try {
            cmd = JsonParser.parseString(line).getAsJsonObject();
        } catch (Exception e) {
            System.err.println("bridge: bad command line: " + (line.length() > 200 ? line.substring(0, 200) + "..." : line));
            return;
        }
        String c = "";
        try {
            c = cmd.has("c") ? cmd.get("c").getAsString() : "";
        } catch (Exception e) {
            System.err.println("bridge: bad command (no command name)");
            return;
        }
        if (c.equals("reply")) {
            Pending p;
            try {
                p = pending.get(cmd.get("id").getAsInt());
            } catch (Exception e) {
                return;                                  // a reply with no usable id: ignored, like a reply nobody asked for
            }
            if (p != null && !p.future.isDone()) {
                Consumer<JsonObject> l = replyListener;
                if (l != null) {
                    l.accept(cmd);
                }
                p.future.complete(cmd.has("value") ? cmd.get("value") : com.google.gson.JsonNull.INSTANCE);
            }
        } else {
            try {
                handler.accept(cmd);
            } catch (Throwable t) {
                System.err.println("bridge: command " + c + " failed");
                t.printStackTrace();
            }
        }
    }

    /** Round MP2: ask every question still waiting for a reply again, oldest first (an online guest has just reconnected; the
     *  old connection may have lost the question or the answer). The ids are unchanged, so a client that already answered
     *  can simply answer again, and a client still showing it can ignore the repeat. */
    public void resendPending() {
        java.util.List<Integer> ids = new java.util.ArrayList<>(pending.keySet());
        java.util.Collections.sort(ids);
        for (Integer id : ids) {
            Pending p = pending.get(id);
            if (p != null && !p.future.isDone()) {
                send(p.msg);
            }
        }
    }

    /** Round MP2b: is question `id` asked and still waiting for its reply? */
    boolean hasPending(int id) {
        Pending p = pending.get(id);
        return p != null && !p.future.isDone();
    }

    /** Round MP2b: answer question `id` as if the client had (a saved online game being played back). False if it isn't waiting. */
    boolean completeReply(int id, JsonElement value) {
        Pending p = pending.get(id);
        return p != null && p.future.complete(value);
    }

    /** Round MP2: how many questions are waiting for a reply (tests and log lines). */
    public int pendingCount() {
        return pending.size();
    }

    public void close() {
        closed = true;
        for (Pending p : pending.values()) {
            p.future.completeExceptionally(new IOException("closed"));
        }
    }

    public boolean isClosed() {
        return closed;
    }
}
