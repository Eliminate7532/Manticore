// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedReader;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;

/**
 * Round MP2b: plays a saved online game back into a new engine, seat by seat (NetHost --replay FILE).
 *
 * The file is NetHost's own journal of the original game: one line per command it applied, {"s":seat,"c":{...}} (seat 1 = the
 * host, 2-4 = guests), in the order it applied them, replies included. With the same seed, the same decks, the same names and
 * the same commands, Forge plays the same game (the local "Resume last game" relies on the same thing). Each command waits for
 * its own seat to reach the same point first:
 *   - a click that says which question it answers ("at", round 22) waits until that seat's question number reaches it; a
 *     number already past means the original click was dropped as stale, so it is skipped;
 *   - a reply waits until that seat has been asked that question (the same id: Forge asks the same questions in the same order);
 *   - anything else (yields, stops, ...) waits until that seat's view has stopped changing for a moment.
 * Every command applied goes into the new journal too, so the game can be saved and resumed again.
 * When one waits too long (STEP_LIMIT), the play-back stops there and says so: the game carries on from that point.
 */
final class ResumeFeeder implements Runnable {
    static long STEP_LIMIT_MS = 120_000;
    static long SETTLE_MS = 150;
    static long POLL_MS = 15;

    static final class Entry {
        final int seat;
        final JsonObject cmd;

        Entry(int seat, JsonObject cmd) {
            this.seat = seat;
            this.cmd = cmd;
        }
    }

    final List<Entry> entries;
    int applied = 0, skipped = 0;
    String diverged = null;

    ResumeFeeder(List<Entry> entries) {
        this.entries = entries;
    }

    /** The journal's lines; a torn last line (the program closed mid-write) is ignored. */
    static List<Entry> read(String path) throws IOException {
        List<Entry> out = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(path), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                try {
                    JsonObject o = JsonParser.parseString(line).getAsJsonObject();
                    out.add(new Entry(o.get("s").getAsInt(), o.getAsJsonObject("c")));
                } catch (Exception e) {
                    System.err.println("nethost: replay: a journal line that can't be read was skipped");
                }
            }
        }
        return out;
    }

    @Override
    public void run() {
        int n = entries.size();
        long lastProgress = 0;
        for (int i = 0; i < n; i++) {
            if (NetHost.gameOver || NetHost.leaving.get()) {
                break;
            }
            Entry e = entries.get(i);
            BridgeGui g = NetHost.guiForSeat(e.seat);
            Wire w = NetHost.wireForSeat(e.seat);
            if (g == null || w == null) {
                diverged = "action " + (i + 1) + ": no seat " + e.seat;
                break;
            }
            String c = NetHost.str(e.cmd, "c");
            try {
                if (c.equals("reply")) {
                    int id = e.cmd.get("id").getAsInt();
                    if (!waitFor(() -> w.hasPending(id))) {
                        diverged = "action " + (i + 1) + ": seat " + e.seat + " was never asked question " + id;
                        break;
                    }
                    NetHost.journal(e.seat, e.cmd);
                    JsonElement value = e.cmd.has("value") ? e.cmd.get("value") : JsonNull.INSTANCE;
                    w.completeReply(id, value);
                    applied++;
                } else if (e.cmd.has("at")) {
                    long at = e.cmd.get("at").getAsLong();
                    if (!waitFor(() -> g.inputSeq() >= at)) {
                        diverged = "action " + (i + 1) + ": seat " + e.seat + " never reached question " + at;
                        break;
                    }
                    settle(g);
                    if (g.inputSeq() > at) {
                        skipped++;                                // the original click was stale and dropped
                        continue;
                    }
                    apply(e, g);
                } else {
                    settle(g);
                    apply(e, g);
                }
            } catch (Exception ex) {
                diverged = "action " + (i + 1) + ": " + ex;
                break;
            }
            long now = System.currentTimeMillis();
            if (now - lastProgress > 250 || i == n - 1) {
                lastProgress = now;
                JsonObject m = new JsonObject();
                m.addProperty("t", "replay");
                m.addProperty("done", i + 1);
                m.addProperty("total", n);
                NetHost.hostWire.send(m);
            }
        }
    }

    /** On Swing's thread, as a live command is - but without waiting for it: a click can make Forge ask a question there and
     *  block until it is answered, and the answer is the next entry, which only this thread can give (invokeAndWait here
     *  deadlocked on the first "choose an ability" of the online soak's first resumed game). Swing runs them in order. */
    void apply(Entry e, BridgeGui g) {
        javax.swing.SwingUtilities.invokeLater(() -> {
            NetHost.journal(e.seat, e.cmd);
            Main.handle(g, e.cmd);
        });
        applied++;
    }

    /** Until the seat's view hasn't changed for SETTLE_MS (the original player only clicked once the screen showed the
     *  question), or STEP_LIMIT passes. */
    static void settle(BridgeGui g) throws InterruptedException {
        long end = System.currentTimeMillis() + STEP_LIMIT_MS;
        long seen = g.flushes, seq = g.inputSeq(), since = System.currentTimeMillis();
        while (System.currentTimeMillis() < end) {
            Thread.sleep(POLL_MS);
            if (g.flushes != seen || g.inputSeq() != seq) {
                seen = g.flushes;
                seq = g.inputSeq();
                since = System.currentTimeMillis();
            } else if (System.currentTimeMillis() - since >= SETTLE_MS) {
                return;
            }
        }
    }

    static boolean waitFor(BooleanSupplier cond) throws InterruptedException {
        long end = System.currentTimeMillis() + STEP_LIMIT_MS;
        while (System.currentTimeMillis() < end) {
            if (cond.getAsBoolean()) {
                return true;
            }
            if (NetHost.gameOver || NetHost.leaving.get()) {
                return false;
            }
            Thread.sleep(POLL_MS);
        }
        return cond.getAsBoolean();
    }
}
