package forge.bridge;

/**
 * The rules for handing out damage (or any other "divide N among these") - plain arithmetic with no Forge
 * types, so the checks can run without an engine. The Python side (allocation.py) follows the same rules
 * to show what is allowed; this side re-checks whatever comes back, because a wrong answer here would
 * put the game in a state the engine never expected.
 *
 * Combat damage rows are the blockers in damage order, then (when the attacker may hit past them) the
 * defending player or planeswalker as the last row.
 */
final class Assign {
    private Assign() { }

    /**
     * Lethal damage to each row first, in order; what is left goes to the last row - the defender when there
     * is one (trample), otherwise the last blocker. This is what the desktop game's "Auto" button does.
     */
    static int[] autoDamage(int total, int[] lethal) {
        int n = lethal.length;
        int[] out = new int[n];
        if (n == 0) return out;
        int left = total;
        for (int i = 0; i < n && left > 0; i++) {
            int give = Math.min(left, Math.max(0, lethal[i]));
            out[i] = give;
            left -= give;
        }
        out[n - 1] += left;
        return out;
    }

    /**
     * null when the split is allowed, else the reason it is not.
     * order: damage must be paid in order (every earlier row lethal before a later row gets any).
     * free: any split at all (an attacker that divides damage as it likes).
     * defenderLast: the last row is the defender; it needs every blocker lethal first, even when the order
     *   among the blockers is free.
     */
    static String checkDamage(int[] amounts, int total, int[] lethal, boolean order, boolean free, boolean defenderLast) {
        if (amounts == null || amounts.length != lethal.length) return "wrong number of rows";
        int sum = 0;
        for (int a : amounts) {
            if (a < 0) return "negative amount";
            sum += a;
        }
        if (sum != total) return "amounts add up to " + sum + ", not " + total;
        if (free) return null;
        boolean allLethalSoFar = true;
        for (int i = 0; i < amounts.length; i++) {
            boolean isDefender = defenderLast && i == amounts.length - 1;
            if (amounts[i] > 0 && !allLethalSoFar && (order || isDefender)) {
                return "damage goes to row " + (i + 1) + " before the rows before it have lethal";
            }
            if (amounts[i] < lethal[i]) allLethalSoFar = false;
        }
        return null;
    }

    /** Give each row 1 when at least one is required, then fill the rows in order up to their maximum. */
    static int[] autoDivide(int total, int[] max, boolean atLeastOne) {
        int n = max.length;
        int[] out = new int[n];
        int left = total;
        if (atLeastOne) {
            for (int i = 0; i < n && left > 0; i++) {
                out[i] = 1;
                left--;
            }
        }
        for (int i = 0; i < n && left > 0; i++) {
            int room = Math.max(0, max[i] - out[i]);
            int give = Math.min(left, room);
            out[i] += give;
            left -= give;
        }
        if (left > 0 && n > 0) out[n - 1] += left;        // the maxima cannot hold it all: the engine asked for the impossible
        return out;
    }

    static String checkDivide(int[] amounts, int total, int[] max, boolean atLeastOne) {
        if (amounts == null || amounts.length != max.length) return "wrong number of rows";
        int sum = 0;
        for (int i = 0; i < amounts.length; i++) {
            int a = amounts[i];
            if (a < 0) return "negative amount";
            if (a > max[i]) return "row " + (i + 1) + " is over its maximum";
            if (atLeastOne && a < 1) return "row " + (i + 1) + " needs at least 1";
            sum += a;
        }
        if (sum != total) return "amounts add up to " + sum + ", not " + total;
        return null;
    }
}
