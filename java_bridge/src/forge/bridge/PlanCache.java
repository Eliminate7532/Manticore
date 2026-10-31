package forge.bridge;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.google.common.collect.Multiset;

import forge.game.card.Card;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.keyword.KeywordInterface;
import forge.game.player.Player;

/**
 * Round AIT2 (23 Oct 2026): the AI's combat predictions without the minutes (round AIT1, docs/rounds/AIT1.md, found where its
 * 20 s went: soak nights 11-21 Oct had 102 "AI eval thread at timeout" samples, 70 of them inside an attack plan).
 *
 * <p>Two things Forge's AI works out again and again on a board that doesn't change while it thinks:
 * <ol>
 * <li><b>Attack plans for identical creatures.</b> PumpAllAi asks ComputerUtilCard.shouldPumpCard about every creature the
 *     ability pumps; before combat on the AI's own turn each question plans a whole attack for that one pumped creature
 *     (doesSpecifiedCreatureAttackAI -> AiAttackController.declareAttackers). Inside PumpAllAi's loop (the bridge's copy of
 *     PumpAllAi opens a "pump" scope), two questions about creatures that are the same in every way an attack plan looks at
 *     (see {@link #kind}) get one plan. Every creature is still asked, so the random draw at the end of shouldPumpCard still
 *     happens once per creature. What changes: an attack plan has random parts of its own (AiAttackController's
 *     chanceToAttackToTrade, ...), and now one plan's draw answers for all the identical creatures instead of one draw each.</li>
 * <li><b>"Can this creature regenerate?"</b> ComputerUtil.canRegenerate walks every regeneration ability its controller has and
 *     pays for it in thought, and a combat prediction asks it for the same creatures many times. Inside any scope (PumpAllAi's
 *     loop, and each ComputerUtil.predictNextCombatsRemainingLife) the answer for a creature is kept. It is the same function of
 *     the same board, so nothing about the answer changes - unless something inside the scope reserves mana (shouldPumpCard's
 *     "hold a combat trick" path for an instant in hand), which Forge's own canRegenerate would then see and this cache would
 *     not.</li>
 * </ol>
 *
 * <p>Modes ({@code --plan-cache}, Main and NetHost): "on" (the default), "off" (no scopes: Forge's own behaviour, for
 * comparison) and "compare" (works everything out as Forge does, and logs how often a kept answer would have been different).
 * Everything is per thread (Forge's AI thinks on its "Game AI Eval" thread).
 */
public final class PlanCache {
    public static volatile String mode = "on";
    static final List<String> MODES = List.of("on", "off", "compare");
    static final int LOG_WHEN_REUSED = 5;          // "on": a line in forge_engine.log only when this many attack plans were saved

    private static final class Scope {
        int depth, pumpDepth;
        final Map<String, Boolean> plans = new HashMap<>();
        final Map<String, Boolean> regen = new HashMap<>();
        int asked, planned, reused, same, differ;
        int regenAsked, regenReused, regenSame, regenDiffer;
        final List<String> differences = new ArrayList<>();
    }

    private static final ThreadLocal<Scope> SCOPE = new ThreadLocal<>();

    private PlanCache() {
    }

    /** A scope starts: pump = PumpAllAi's loop (attack plans are shared too), else a block prediction (regeneration only).
     *  Nested scopes are one scope (the outermost ends it). */
    public static void begin(boolean pump) {
        if ("off".equals(mode)) {
            return;
        }
        Scope s = SCOPE.get();
        if (s == null) {
            s = new Scope();
            SCOPE.set(s);
        }
        s.depth++;
        if (pump) {
            s.pumpDepth++;
        }
    }

    /** PumpAllAi's loop ended (also when it threw). */
    public static void endPump(Card source) {
        Scope s = SCOPE.get();
        if (s != null && s.pumpDepth > 0) {
            s.pumpDepth--;
        }
        end(source);
    }

    /** A block prediction ended (also when it threw). */
    public static void end() {
        end(null);
    }

    private static void end(Card source) {
        Scope s = SCOPE.get();
        if (s == null || --s.depth > 0) {
            return;
        }
        SCOPE.remove();
        if (source == null) {
            if ("compare".equals(mode) && s.regenDiffer > 0) {
                System.err.println("plancache: compare (block prediction): regeneration asked=" + s.regenAsked
                        + " repeats=" + (s.regenSame + s.regenDiffer) + " differ=" + s.regenDiffer + " first_differ=" + s.differences);
            }
            return;
        }
        String name = source.getName();
        if ("compare".equals(mode)) {
            System.err.println("plancache: compare " + name + ": asked=" + s.asked + " planned=" + s.planned
                    + " repeats=" + (s.same + s.differ) + " same=" + s.same + " differ=" + s.differ
                    + " regeneration asked=" + s.regenAsked + " repeats=" + (s.regenSame + s.regenDiffer)
                    + " differ=" + s.regenDiffer + (s.differences.isEmpty() ? "" : " first_differ=" + s.differences));
        } else if (s.reused >= LOG_WHEN_REUSED || s.regenReused >= 100 * LOG_WHEN_REUSED) {
            System.err.println("plancache: " + name + ": " + s.asked + " attack questions, " + s.planned + " planned, "
                    + s.reused + " answered from an identical creature's plan; regeneration asked " + s.regenAsked + ", "
                    + s.regenReused + " answered from memory");
        }
    }

    // ---- attack plans (ComputerUtilCard.doesSpecifiedCreatureAttackAI) ----------------------------------------------------

    /** The answer for this creature if an identical one was planned in this pump scope; null = plan it. */
    public static Boolean known(Player ai, Card card) {
        Scope s = SCOPE.get();
        if (s == null || s.pumpDepth == 0) {
            return null;
        }
        s.asked++;
        if ("compare".equals(mode)) {
            return null;
        }
        String k = key(ai, card);
        Boolean b = k == null ? null : s.plans.get(k);
        if (b != null) {
            s.reused++;
        }
        return b;
    }

    /** What a plan said (returned unchanged). */
    public static boolean remember(Player ai, Card card, boolean attacks) {
        Scope s = SCOPE.get();
        if (s == null || s.pumpDepth == 0) {
            return attacks;
        }
        s.planned++;
        String k = key(ai, card);
        if (k == null) {
            return attacks;
        }
        Boolean before = s.plans.putIfAbsent(k, attacks);
        if (before != null) {
            if (before == attacks) {
                s.same++;
            } else {
                s.differ++;
                if (s.differences.size() < 3) {
                    s.differences.add("plan " + card.getName() + " " + before + "->" + attacks);
                }
            }
        }
        return attacks;
    }

    // ---- regeneration (ComputerUtil.canRegenerate) --------------------------------------------------------------------

    /** The kept answer for this creature in this scope; null = work it out. */
    public static Boolean regenerationKnown(Player ai, Card card) {
        AttackCache.checkBudget();                     // round AI-ATTACK: the attack declaration's budget, see there
        Scope s = SCOPE.get();
        if (s == null) {
            return null;
        }
        s.regenAsked++;
        if ("compare".equals(mode)) {
            return null;
        }
        Boolean b = s.regen.get(regenKey(ai, card));
        if (b != null) {
            s.regenReused++;
        }
        return b;
    }

    /** What canRegenerate worked out (returned unchanged). */
    public static boolean regeneration(Player ai, Card card, boolean can) {
        Scope s = SCOPE.get();
        if (s == null) {
            return can;
        }
        Boolean before = s.regen.putIfAbsent(regenKey(ai, card), can);
        if (before != null) {
            if (before == can) {
                s.regenSame++;
            } else {
                s.regenDiffer++;
                if (s.differences.size() < 3) {
                    s.differences.add("regenerate " + card.getName() + " " + before + "->" + can);
                }
            }
        }
        return can;
    }

    /** A card and the player asking (a pumped copy keeps its creature's id, and Forge counts the two as the same card). */
    static String regenKey(Player ai, Card card) {
        return (ai == null ? "-" : String.valueOf(ai.getId())) + ":" + card.getId();
    }

    /** The question's key: the card asked about (a pumped copy keeps its creature's id) and that creature on the battlefield.
     *  Null when the creature isn't one of the AI's on the battlefield (then nothing is shared). */
    static String key(Player ai, Card card) {
        Card original = null;
        for (Card c : ai.getCreaturesInPlay()) {
            if (c.getId() == card.getId()) {
                original = c;
                break;
            }
        }
        if (original == null) {
            return null;
        }
        return kind(card) + " # " + kind(original);
    }

    /** Everything about a creature that an attack plan looks at, apart from which card it is. */
    static String kind(Card c) {
        StringBuilder b = new StringBuilder(c.getName());
        b.append('|').append(c.getNetPower()).append('/').append(c.getNetToughness());
        b.append('|').append(c.isTapped() ? 'T' : '-').append(c.hasSickness() ? 'S' : '-').append(c.isGoaded() ? 'G' : '-')
                .append(c.isFaceDown() ? 'F' : '-').append(c.isToken() ? 'K' : '-').append(c.isPhasedOut() ? 'P' : '-');
        b.append('|').append(c.getDamage());
        List<String> parts = new ArrayList<>();
        for (Multiset.Entry<CounterType> e : c.getCounters().entrySet()) {
            parts.add(e.getElement() + "=" + e.getCount());
        }
        Collections.sort(parts);
        b.append('|').append(parts);
        parts = new ArrayList<>();
        for (KeywordInterface k : c.getKeywords()) {
            parts.add(k.getOriginal());
        }
        Collections.sort(parts);
        b.append('|').append(parts);
        parts = new ArrayList<>();
        for (Card a : c.getAttachedCards()) {
            parts.add(a.getName());
        }
        Collections.sort(parts);
        b.append('|').append(parts);
        Combat combat = c.getGame() == null ? null : c.getGame().getCombat();
        if (combat != null) {
            boolean attacking = combat.isAttacking(c), blocking = combat.isBlocking(c);
            b.append("|atk=").append(attacking).append(" blk=").append(blocking);
            parts = new ArrayList<>();
            if (attacking) {
                for (Card o : combat.getBlockers(c)) {
                    parts.add(String.valueOf(o.getId()));
                }
            }
            if (blocking) {
                for (Card o : combat.getAttackersBlockedBy(c)) {
                    parts.add("a" + o.getId());
                }
            }
            Collections.sort(parts);
            b.append(parts);
        }
        return b.toString();
    }
}
