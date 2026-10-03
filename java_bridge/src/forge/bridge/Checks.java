package forge.bridge;

import com.google.gson.JsonObject;

import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Round 27d: the bridge checks its own answers before Forge acts on them.
 *
 * Each of Forge's questions to the human (order these, choose some of these, yes or no, ...) comes with rules the answer must
 * keep: an "order all of these" answer contains every item exactly once; a "choose 1 to 3" answer has 1 to 3 different items
 * from the list; and so on. Forge trusts the answer. If the bridge breaks a rule, Forge usually carries on quietly with a
 * wrong game (round 27c: an empty order put NO triggers on the stack, and nothing anywhere said so).
 *
 * A broken rule is reported two ways, and the answer itself is left as it was (these checks watch; they don't repair):
 *   - one "bridge: CHECK FAILED ..." line in the engine log (forge_engine.log, which bug reports include);
 *   - a {"t":"check", "q", "rule", "detail"} line to the Python side (ForgeSession.checks; tests fail on any).
 *
 * The rules are written from what Forge's callers do with the answer (PlayerControllerHuman and AbstractGuiGame at Forge
 * 3a74143), not from how BridgeGui computes it, so a mistake in BridgeGui's own logic is caught rather than repeated.
 *
 * Each question also sends a {"t":"shape", "q", "shape"} line: what KIND of question it was (e.g. "order all, 2 already sorted,
 * 0 unsorted"). Collected over many games, the shapes show which kinds of question have never been exercised.
 *
 * FAULTS (dev only): Main's --fault NAME (with --dev) makes BridgeGui deliberately misbehave in one known way, so a test can
 * prove a check goes red without needing a JDK to build a broken jar. See Fault below.
 */
final class Checks {
    private Checks() { }

    /** Known deliberate faults for tests. Only honoured when the bridge runs with --dev. */
    enum Fault {
        ORDER_IGNORES_SORTED,       // the round 27c bug: order() reads only sourceChoices
        CHOICES_OVER_MAX,           // getChoices answers one item more than allowed
        NO_COMMANDER_VARIANT        // the round 27c bug: the match starts without the Commander variant
    }

    static final Set<Fault> faults = java.util.EnumSet.noneOf(Fault.class);

    static boolean fault(Fault f) {
        return Main.dev && faults.contains(f);
    }

    static volatile Wire wire;
    static final java.util.concurrent.atomic.AtomicInteger failures = new java.util.concurrent.atomic.AtomicInteger();

    static void fail(String q, String rule, String detail) {
        failures.incrementAndGet();
        System.err.println("bridge: CHECK FAILED " + q + " / " + rule + ": " + detail);
        Wire w = wire;
        if (w != null) {
            JsonObject m = new JsonObject();
            m.addProperty("t", "check");
            m.addProperty("q", q);
            m.addProperty("rule", rule);
            m.addProperty("detail", detail);
            w.send(m);
        }
    }

    static void shape(String q, String shape) {
        Wire w = wire;
        if (w != null) {
            JsonObject m = new JsonObject();
            m.addProperty("t", "shape");
            m.addProperty("q", q);
            m.addProperty("shape", shape);
            w.send(m);
        }
    }

    /** 0, 1 or "2+": shapes stay a short list however big the game gets. */
    static String bucket(int n) {
        return n <= 0 ? "0" : n == 1 ? "1" : "2+";
    }

    static int size(Collection<?> c) {
        return c == null ? 0 : c.size();
    }

    /** Every answered item came from the offered items, and none twice. Returns a problem, or null. */
    static String subsetProblem(List<?> answer, Collection<?> offered) {
        if (answer == null) return null;
        Set<Object> seen = new HashSet<>();
        for (Object o : answer) {
            if (offered == null || !offered.contains(o)) return "answered an item that was not offered: " + o;
            if (!seen.add(o)) return "answered the same item twice: " + o;
        }
        return null;
    }

    // ---- the rules, one method per kind of question -------------------------------------------------------------------

    /**
     * order(): Forge offers `unsorted` (sourceChoices) and `sorted` (destChoices, already in order - e.g. last time's order of
     * the same triggers). remainingMin/Max: how many may stay unchosen (-1 = no limit). (0, 0) means "put ALL of them in order":
     * callers such as orderSimultaneousSa then act on exactly the returned items, so a missing item is a missing trigger.
     */
    static <T> void order(String title, int remainingMin, int remainingMax, List<T> unsorted, List<T> sorted, List<T> answer) {
        int total = size(unsorted) + size(sorted);
        String orderAll = remainingMin == 0 && remainingMax == 0 ? "all" : "some";
        shape("order", orderAll + ":sorted=" + bucket(size(sorted)) + ":unsorted=" + bucket(size(unsorted)));
        Set<Object> offered = new HashSet<>();
        if (unsorted != null) offered.addAll(unsorted);
        if (sorted != null) {
            for (T t : sorted) {
                if (!offered.add(t)) fail("order", "offered_twice", "Forge offered an item as both sorted and unsorted: " + t);
            }
        }
        if (answer == null) {
            if (total > 0) fail("order", "no_answer", title + ": returned null for " + total + " items");
            return;
        }
        String p = subsetProblem(answer, offered);
        if (p != null) fail("order", "not_from_offer", title + ": " + p);
        int lo = remainingMax < 0 ? 0 : Math.max(0, total - remainingMax);
        int hi = remainingMin < 0 ? total : Math.max(0, total - remainingMin);
        if (answer.size() < lo || answer.size() > hi) {
            fail("order", "wrong_count", title + ": answered " + answer.size() + " of " + total + " items (" + size(sorted)
                    + " already sorted), allowed " + lo + ".." + hi);
        }
    }

    /** getChoices(min, max): Forge's one()/oneOrNone()/getInteger() and many callers read exactly what comes back. */
    static <T> void choices(String title, int min, int max, List<T> offered, List<T> answer) {
        shape("choose", "min=" + bucket(min) + ":max=" + (max == Integer.MAX_VALUE ? "any" : bucket(max)) + ":offered=" + bucket(size(offered)));
        if (answer == null) {
            fail("choose", "no_answer", title + ": returned null");
            return;
        }
        String p = subsetProblem(answer, offered);
        if (p != null) fail("choose", "not_from_offer", title + ": " + p);
        int lo = Math.min(min, size(offered));
        if (answer.size() < lo || answer.size() > max) {
            fail("choose", "wrong_count", title + ": answered " + answer.size() + ", allowed " + lo + ".." + max);
        }
    }

    /** chooseSingleEntityForEffect: an item from the list; null only when optional. */
    static void single(String title, List<?> offered, Object answer, boolean optional) {
        shape("choose_one", (optional ? "optional" : "required") + ":offered=" + bucket(size(offered)));
        if (answer == null) {
            if (!optional && size(offered) > 0) fail("choose_one", "missing_required", title + ": nothing chosen from " + size(offered));
        } else if (!offered.contains(answer)) {
            fail("choose_one", "not_from_offer", title + ": " + answer);
        }
    }

    /** The client answered something the bridge could not use, so a default was used instead. Forge never sees a problem. */
    static void unusable(String q, String what) {
        fail(q, "client_answer_unusable", what);
    }

    /** Forge asked the human and the bridge answered by itself (no dialog exists for this yet). */
    static void autoAnswered(String q, String what) {
        fail(q, "auto_answered", what);
    }
}
