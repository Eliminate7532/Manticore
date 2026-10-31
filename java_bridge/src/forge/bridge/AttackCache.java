package forge.bridge;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.tuple.Pair;

import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardLists;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.player.Player;
import forge.game.staticability.StaticAbility;
import forge.game.staticability.StaticAbilityCantAttackBlock;
import forge.game.staticability.StaticAbilityMode;
import forge.game.zone.ZoneType;
import forge.util.AITimeoutTracker;

/**
 * Round AI-ATTACK (27 Oct 2026): the AI's attack planner on a huge board without the half hour.
 *
 * <p>Round TOKENS (claude/TOKENS_2026-10-27.md) measured an AI turn with 1,023 tokens a side that didn't finish in 30 minutes,
 * all of it in AiAttackController.doAssault: for every attacker it asks CombatUtil.canBeBlocked and getPotentialBestBlockers
 * over every blocker, then asks canBlock for every (blocker, attacker) pair again. Each canBlock walks the static abilities of
 * every card in the game twice (StaticAbilityCantAttackBlock.cantBlock for the blocker, cantBlockBy for the pair), and each
 * comparison of getPotentialBestBlockers' sort by power walks them twice more (Card.getNetCombatDamage). So the work grows with
 * (creatures)^3: about 10 times per doubling of the board.
 *
 * <p>doAssault only looks: it changes nothing in the game or the combat. So inside it (the bridge's copy of AiAttackController
 * opens a scope around the one call to doAssault) the answers that depend only on the board are worked out once:
 * <ul>
 * <li>"can this creature block at all" (CombatUtil.canBlock(blocker, false)), per creature;</li>
 * <li>each card's static abilities, and the list of every static ability in the game's static-ability zones that can say
 *     "can't block" (checkConditions(CantBlockBy)), and of those the ones that apply to a given attacker (their ValidAttacker
 *     matches it - the first thing applyCantBlockByAbility checks); a pair then checks only those, the attacker's own and the
 *     blocker's own, with Forge's own applyCantBlockByAbility - the same abilities Forge's cantBlockBy checks, in another order
 *     (it's an "any of them" question, so the order doesn't change the answer);</li>
 * <li>Card.getNetCombatDamage per card (the sort key), and getMinMaxBlocker per (attacker, defender).</li>
 * </ul>
 * The methods below give the same answers as CombatUtil's canBlock(attacker, blocker), canBeBlocked(attacker, blockers, null),
 * getPotentialBestBlockers(attacker, blockers, null) and CardLists.sortByPowerDesc, and outside a scope they ARE those methods.
 *
 * <p>Modes ({@code --attack-cache}, Main and NetHost): "on" (the default), "off" (Forge's own: no scope) and "compare" (works
 * everything out both ways, uses Forge's answers, and logs per doAssault how many answers were asked and how many differed).
 * Scopes are per thread (doAssault runs on the thread that plans the attack).
 */
public final class AttackCache {
    public static volatile String mode = "on";
    static final List<String> MODES = List.of("on", "off", "compare");
    static final long LOG_WHEN_PAIRS = 10000;      // "on": a line in forge_engine.log for a doAssault over this many pairs or more
    static volatile int plantedError = 0;          // tests only (--attack-cache-plant N, with --dev): 1 = every 97th canBlock
                                                   // answer (the first of each plan included) turned round

    private static final class Scope {
        int depth;
        Game game;
        List<StaticAbility> blockByAll;                                     // every CantBlockBy ability that may apply now
        final Map<Card, List<StaticAbility>> blockByFor = new IdentityHashMap<>();   // ... of those, the ones for this attacker
        final Map<Card, List<StaticAbility>> statics = new IdentityHashMap<>();
        final Map<Card, Boolean> canBlockAtAll = new IdentityHashMap<>();
        final Map<Card, Integer> netDamage = new IdentityHashMap<>();
        final Map<Card, Map<Player, Pair<Integer, Integer>>> minMax = new IdentityHashMap<>();
        long t0, pairs;
        int attackers, blockers;
        long asked, differ, listsAsked, listsDiffer, sorts, sortsDiffer;
        final List<String> differences = new ArrayList<>();
    }

    private static final ThreadLocal<Scope> SCOPE = new ThreadLocal<>();

    /** --attack-budget N: seconds the AI's whole attack declaration may take (0 = no budget, Forge's own behaviour). */
    public static volatile int budgetSeconds = 30;
    private static final ThreadLocal<long[]> BUDGET_END = new ThreadLocal<>();   // this thread's budget deadline (System.nanoTime)

    private AttackCache() {
    }

    /** doAssault starts (the AI's own attackers and the defender's possible blockers, for the log line only). */
    public static void begin(Player ai, int attackers, int blockers) {
        if ("off".equals(mode)) {
            return;
        }
        Scope s = SCOPE.get();
        if (s == null) {
            s = new Scope();
            s.game = ai.getGame();
            s.t0 = System.nanoTime();
            s.attackers = attackers;
            s.blockers = blockers;
            SCOPE.set(s);
        }
        s.depth++;
    }

    /** doAssault ended (also when it threw). */
    public static void end() {
        Scope s = SCOPE.get();
        if (s == null) {
            return;
        }
        if (--s.depth > 0) {
            return;
        }
        SCOPE.remove();
        long ms = (System.nanoTime() - s.t0) / 1_000_000;
        if ("compare".equals(mode)) {
            System.err.println("attackcache: compare " + s.attackers + " attackers x " + s.blockers + " blockers: canBlock asked="
                    + s.asked + " differ=" + s.differ + " lists asked=" + s.listsAsked + " differ=" + s.listsDiffer
                    + " sorts=" + s.sorts + " differ=" + s.sortsDiffer + " in " + ms + " ms"
                    + (s.differences.isEmpty() ? "" : " first: " + String.join("; ", s.differences)));
        } else if ((long) s.attackers * s.blockers >= LOG_WHEN_PAIRS) {
            System.err.println("attackcache: " + s.attackers + " attackers x " + s.blockers + " blockers planned in " + ms
                    + " ms (canBlock asked " + s.asked + " times)");
        }
    }

    // ---- the time budget of the AI's attack declaration ----

    /**
     * The AI's attack declaration (PlayerControllerAi.declareAttackers, run as {@code forge}) with a hard deadline.
     *
     * <p>Forge 9a6f776 already gives the attack plan its AI think limit (AiController.declareAttackers arms
     * AITimeoutTracker for game.getAITimeout() seconds and, past it, attacks with what was decided so far) - but it makes the
     * AiAttackController first, and with more than one opponent that constructor picks the defender by predicting each
     * opponent's next combat (choosePreferredDefenderPlayer -> ComputerUtil.evaluateBoardPosition ->
     * predictNextCombatsRemainingLife -> AiBlockController), unarmed. With 255 tokens a side in a 4-player game that took over
     * five minutes (round AI-ATTACK's bench). Here the whole declaration is armed with {@link #budgetSeconds} (30 s: Forge's
     * own 20 s limit starts later and is tighter, so this one only shortens a declaration whose start alone took over 10 s). The
     * block predictions check the deadline (AITimeoutTracker.check), so they stop at it; if that happens before Forge's own
     * fallback could run, the AI declares only what it must: the attack it had so far if it is legal, else Forge's legal
     * minimum (creatures that must attack), as AiController does for an invalid declaration.
     */
    static void declareWithBudget(Player ai, Combat combat, Runnable forge) {
        int secs = budgetSeconds;
        if (secs <= 0) {
            forge.run();
            return;
        }
        long t0 = System.nanoTime();
        long[] before = BUDGET_END.get();
        BUDGET_END.set(new long[] {t0 + secs * 1_000_000_000L});
        Object limit = AITimeoutTracker.arm(t0 + secs * 1_000_000_000L, false);
        try {
            forge.run();
        } catch (AITimeoutTracker.Expired e) {
            fallBack(ai, combat, secs, t0);
        } finally {
            AITimeoutTracker.disarm(limit);
            BUDGET_END.set(before);
        }
    }

    /**
     * Forge's block predictions check their deadline once per attacker, and on a big board one attacker's turn of the loop
     * takes seconds (each blocker against it: canDestroyBlocker, canDestroyAttacker ...). Those ask "can it regenerate?" for
     * every pair (ComputerUtil.canRegenerate, the bridge's copy, asks PlanCache, which asks this): once this thread is past
     * its attack-declaration budget, AITimeoutTracker.check() stops there too. Before the budget's end, and on any thread
     * outside an attack declaration, this does nothing.
     */
    public static void checkBudget() {
        long[] end = BUDGET_END.get();
        if (end != null && System.nanoTime() - end[0] >= 0) {
            AITimeoutTracker.check();          // armed with a deadline no later than this one, so it throws
        }
    }

    private static void fallBack(Player ai, Combat combat, int secs, long t0) {
        String what;
        if (CombatUtil.validateAttackers(combat)) {
            what = combat.getAttackers().size() + " attacker(s) decided so far";
        } else {
            combat.clearAttackers();
            Map<Card, GameEntity> legal = combat.getAttackConstraints().getLegalAttackers().getLeft();
            for (Map.Entry<Card, GameEntity> e : legal.entrySet()) {
                combat.addAttacker(e.getKey(), e.getValue());
            }
            what = "the " + legal.size() + " that must attack";
        }
        System.err.println("attackcache: " + ai.getName() + "'s attack plan passed its " + secs + " s budget ("
                + (System.nanoTime() - t0) / 1_000_000 + " ms): declared " + what);
    }

    static boolean active() {
        return SCOPE.get() != null;
    }

    // ---- the four questions doAssault asks ----

    /** CombatUtil.canBlock(attacker, blocker). */
    public static boolean canBlock(final Card attacker, final Card blocker) {
        Scope s = SCOPE.get();
        if (s == null) {
            return CombatUtil.canBlock(attacker, blocker);
        }
        s.asked++;
        boolean mine = cachedCanBlock(s, attacker, blocker);
        if ("compare".equals(mode)) {
            boolean forge = CombatUtil.canBlock(attacker, blocker);
            if (forge != mine) {
                s.differ++;
                note(s, "canBlock " + name(attacker) + " <- " + name(blocker) + " forge=" + forge);
            }
            return forge;
        }
        return mine;
    }

    /** CombatUtil.canBeBlocked(attacker, blockers, combat), as doAssault asks it (combat null). */
    public static boolean canBeBlocked(final Card attacker, final List<Card> blockers, final Combat combat) {
        Scope s = SCOPE.get();
        if (s == null || combat != null) {
            return CombatUtil.canBeBlocked(attacker, blockers, combat);
        }
        int blocks = 0;
        for (final Card blocker : blockers) {
            if (canBlock(attacker, blocker)) {
                blocks++;
            }
        }
        // CombatUtil.canAttackerBeBlockedWithAmount(attacker, blocks, (Player) null)
        boolean mine;
        if (blocks == 0) {
            mine = false;
        } else {
            Pair<Integer, Integer> mm = minMax(s, attacker, null);
            mine = !(mm.getLeft() > blocks || mm.getRight() < blocks);
        }
        if ("compare".equals(mode)) {
            s.listsAsked++;
            boolean forge = CombatUtil.canBeBlocked(attacker, blockers, combat);
            if (forge != mine) {
                s.listsDiffer++;
                note(s, "canBeBlocked " + name(attacker) + " forge=" + forge);
            }
            return forge;
        }
        return mine;
    }

    /** CombatUtil.getPotentialBestBlockers(attacker, blockers, combat). */
    public static List<Card> getPotentialBestBlockers(final Card attacker, final List<Card> blockers, final Combat combat) {
        Scope s = SCOPE.get();
        if (s == null) {
            return CombatUtil.getPotentialBestBlockers(attacker, blockers, combat);
        }
        List<Card> potentialBlockers = new ArrayList<>();
        List<Card> mine = new ArrayList<>();
        if (!blockers.isEmpty() && attacker != null) {
            for (final Card blocker : blockers) {
                if (canBlock(attacker, blocker)) {
                    potentialBlockers.add(blocker);
                }
            }
            int minBlockers = minMax(s, attacker, blockers.get(0).getController()).getLeft();
            sortByPowerDesc(s, potentialBlockers);
            for (int i = 0; i < minBlockers && i < potentialBlockers.size(); i++) {
                mine.add(potentialBlockers.get(i));
            }
        }
        if ("compare".equals(mode)) {
            s.listsAsked++;
            List<Card> forge = CombatUtil.getPotentialBestBlockers(attacker, blockers, combat);
            if (!sameCards(forge, mine)) {
                s.listsDiffer++;
                note(s, "bestBlockers " + name(attacker) + " forge=" + forge.size() + " mine=" + mine.size());
            }
            return forge;
        }
        return mine;
    }

    /** CardLists.sortByPowerDesc(list): the same stable sort, with each card's getNetCombatDamage worked out once. */
    public static void sortByPowerDesc(final List<Card> list) {
        Scope s = SCOPE.get();
        if (s == null) {
            CardLists.sortByPowerDesc(list);
            return;
        }
        if ("compare".equals(mode)) {
            List<Card> mine = new ArrayList<>(list);
            sortByPowerDesc(s, mine);
            CardLists.sortByPowerDesc(list);
            s.sorts++;
            if (!sameCards(list, mine)) {
                s.sortsDiffer++;
                note(s, "sortByPowerDesc of " + list.size());
            }
            return;
        }
        sortByPowerDesc(s, list);
    }

    // ---- the kept answers ----

    private static void sortByPowerDesc(Scope s, List<Card> list) {
        list.sort(Collections.reverseOrder(Comparator.comparingInt(c -> netDamage(s, c))));
    }

    private static int netDamage(Scope s, Card c) {
        Integer v = s.netDamage.get(c);
        if (v == null) {
            v = c.getNetCombatDamage();
            s.netDamage.put(c, v);
        }
        return v;
    }

    private static Pair<Integer, Integer> minMax(Scope s, Card attacker, Player defender) {
        Map<Player, Pair<Integer, Integer>> m = s.minMax.computeIfAbsent(attacker, k -> new java.util.HashMap<>());
        Pair<Integer, Integer> p = m.get(defender);
        if (p == null) {
            Pair<Integer, Integer> got = StaticAbilityCantAttackBlock.getMinMaxBlocker(attacker, defender);
            p = Pair.of(got.getLeft(), got.getRight());
            m.put(defender, p);
        }
        return p;
    }

    /** CombatUtil.canBlock(attacker, blocker, false), from the kept parts. */
    private static boolean cachedCanBlock(Scope s, Card attacker, Card blocker) {
        if (attacker == null || blocker == null || !blocker.isCreature()) {
            return false;
        }
        Boolean atAll = s.canBlockAtAll.get(blocker);
        if (atAll == null) {
            atAll = CombatUtil.canBlock(blocker, false);
            s.canBlockAtAll.put(blocker, atAll);
        }
        if (!atAll) {
            return false;
        }
        boolean can = !cantBlockBy(s, attacker, blocker);
        if (plantedError == 1 && s.asked % 97 == 1) {
            can = !can;                                   // tests: a wrong cached answer, which compare mode must report
        }
        return can;
    }

    /** StaticAbilityCantAttackBlock.cantBlockBy(attacker, blocker): Forge checks the attacker's and the blocker's own static
     *  abilities and then every card's in the static-ability zones; here the last ones come from the list kept for this attacker. */
    private static boolean cantBlockBy(Scope s, Card attacker, Card blocker) {
        for (StaticAbility st : statics(s, attacker)) {
            if (st.checkConditions(StaticAbilityMode.CantBlockBy)
                    && StaticAbilityCantAttackBlock.applyCantBlockByAbility(st, attacker, blocker)) {
                return true;
            }
        }
        for (StaticAbility st : statics(s, blocker)) {
            if (st.checkConditions(StaticAbilityMode.CantBlockBy)
                    && StaticAbilityCantAttackBlock.applyCantBlockByAbility(st, attacker, blocker)) {
                return true;
            }
        }
        for (StaticAbility st : blockByFor(s, attacker)) {
            if (StaticAbilityCantAttackBlock.applyCantBlockByAbility(st, attacker, blocker)) {
                return true;
            }
        }
        return false;
    }

    private static List<StaticAbility> statics(Scope s, Card c) {
        List<StaticAbility> l = s.statics.get(c);
        if (l == null) {
            l = new ArrayList<>();
            for (StaticAbility st : c.getStaticAbilities()) {
                l.add(st);
            }
            s.statics.put(c, l);
        }
        return l;
    }

    private static List<StaticAbility> blockByFor(Scope s, Card attacker) {
        List<StaticAbility> l = s.blockByFor.get(attacker);
        if (l != null) {
            return l;
        }
        if (s.blockByAll == null) {
            s.blockByAll = new ArrayList<>();
            for (final Card ca : s.game.getCardsIn(ZoneType.STATIC_ABILITIES_SOURCE_ZONES)) {
                for (StaticAbility st : statics(s, ca)) {
                    if (st.checkConditions(StaticAbilityMode.CantBlockBy)) {
                        s.blockByAll.add(st);
                    }
                }
            }
        }
        l = new ArrayList<>();
        for (StaticAbility st : s.blockByAll) {
            if (st.matchesValidParam("ValidAttacker", attacker)) {   // applyCantBlockByAbility's first check
                l.add(st);
            }
        }
        s.blockByFor.put(attacker, l);
        return l;
    }

    // ---- helpers ----

    private static boolean sameCards(List<Card> a, List<Card> b) {
        if (a.size() != b.size()) {
            return false;
        }
        for (int i = 0; i < a.size(); i++) {
            if (a.get(i) != b.get(i)) {
                return false;
            }
        }
        return true;
    }

    private static void note(Scope s, String what) {
        if (s.differences.size() < 3) {
            s.differences.add(what);
        }
    }

    private static String name(Card c) {
        return c == null ? "null" : c.getName() + " (" + c.getId() + ")";
    }
}
