// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonObject;

/**
 * Round PERF1 (Karl, 3 Nov 2026: "Waiting after I act" on a big board): timing lines for tools/bench_board.py. Off unless the
 * bridge is started with {@code --timing}; when off, every call here returns at once and nothing is sent.
 *
 * Each line is {"t":"timing","k":<step>,"ms":<duration, ms, may be fractional>,"at":<wall clock ms when it ended>, ...}.
 * Wall clock (currentTimeMillis) so Python, on the same computer, can put the bridge's steps between its own send and receive.
 * Steps: rx (a command line read from stdin), handle (Main.handle on Swing's thread, with "queued" = how long it waited for
 * Swing), decide (Passing.decide, with "d" = PASS/STOP/DEFER), answer (Passing.hasAnswer), forge (PlayerControllerHuman's own
 * chooseSpellAbilityToPlay up to the point it returns or shows the question: its AvailableActions scan; "ret" = "pass" when it
 * passed by itself), ai (one AI seat's chooseSpellAbilityToPlay), snap (Snapshot.build, with "bytes" and "send" = writing it),
 * input (Forge showed the human a new question; ms 0).
 */
final class Timing {
    static volatile boolean on = false;
    static volatile Wire wire;

    private Timing() {}

    static long start() {
        return on ? System.nanoTime() : 0L;
    }

    static double since(long t0) {
        return (System.nanoTime() - t0) / 1_000_000.0;
    }

    static JsonObject line(String k, double ms) {
        JsonObject o = new JsonObject();
        o.addProperty("t", "timing");
        o.addProperty("k", k);
        o.addProperty("ms", Math.round(ms * 100) / 100.0);
        o.addProperty("at", System.currentTimeMillis());
        return o;
    }

    static void send(JsonObject o) {
        Wire w = wire;
        if (!on || w == null) {
            return;
        }
        try {
            w.send(o);
        } catch (RuntimeException e) {
            // timing lines are only information
        }
    }

    /** --sample FILE: every few ms, the stacks of Forge's game threads, one line each ("wall ms|thread|frame;frame;..."),
     *  innermost frame first. A profiler that needs nothing but the JDK (tools/bench_board.py --sample). Dev only. */
    static void startSampler(String path, int everyMs) {
        Thread t = new Thread(() -> {
            try (java.io.PrintWriter out = new java.io.PrintWriter(new java.io.FileWriter(path))) {
                while (true) {
                    long now = System.currentTimeMillis();
                    for (java.util.Map.Entry<Thread, StackTraceElement[]> e : Thread.getAllStackTraces().entrySet()) {
                        String name = e.getKey().getName();
                        if (!name.startsWith("Game-") || e.getKey().getState() != Thread.State.RUNNABLE) {
                            continue;
                        }
                        StringBuilder sb = new StringBuilder();
                        sb.append(now).append('|').append(name).append('|');
                        for (StackTraceElement f : e.getValue()) {
                            sb.append(f.getClassName()).append('.').append(f.getMethodName()).append(';');
                        }
                        out.println(sb);
                    }
                    out.flush();
                    Thread.sleep(everyMs);
                }
            } catch (Exception e) {
                System.err.println("bridge: timing sampler stopped: " + e);
            }
        }, "timing-sampler");
        t.setDaemon(true);
        t.start();
    }

    static void mark(String k, long t0) {
        if (on) {
            send(line(k, since(t0)));
        }
    }
}
