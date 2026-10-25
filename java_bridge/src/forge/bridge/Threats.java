package forge.bridge;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonObject;

import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.game.Game;
import forge.game.ability.ApiType;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.TargetChoices;
import forge.game.zone.ZoneType;
import forge.item.IPaperCard;
import forge.localinstance.properties.ForgeConstants;

/**
 * Round AI2 (Karl, 18 Oct 2026: "AI baseline + AI2"; 13 Oct: "no pod-specific AI work, the AI aims at generic threats"): the
 * AI opponents answer the most dangerous thing on the table, judged from two lists Forge itself ships and keeps up to date -
 * not from any list of ours, and not from Karl's pod.
 *
 *   res/lists/commander-bracket-combos.txt  two-card combos ("early_game | Kinnan, Bonder Prodigy | Basalt Monolith"), 2,172 of
 *                                           them at fb4d809; Forge uses the list for Commander brackets.
 *   res/lists/gamechangers.txt              Wizards' Commander "Game Changers" (53 at fb4d809).
 *
 * A card is a THREAT when it completes a two-card combo with a card its controller already has on the battlefield (a spell being
 * cast; or, for a spell only, a commander in its controller's command zone: always one cast away), or when it is a Game Changer.
 * A permanent is a combo PIECE when the other card of a listed pair is on the same player's battlefield.
 *
 * Forge's AI judges a spell by its mana value and a creature by its size (claude/ROUND_AI1_2026-10-02.md: it counters Arcane
 * Signet as readily as Kinnan, exiles Consecrated Sphinx instead of Kinnan beside Basalt Monolith). What this changes, on top of
 * Forge's own choice (Controller.chooseSpellAbilityToPlay, one AI decision at a time, on the game thread):
 *  1. an opponent's spell on top of the stack is a threat and Forge chose not to counter it -> the AI counters it, if it holds a
 *     counterspell it can cast and pay for now;
 *  2. Forge chose to counter cheap ramp that is no threat (a permanent with a mana ability, mana value 2 or less: Arcane Signet,
 *     Llanowar Elves) with a counterSPELL -> the AI passes and keeps it (a counter that costs no card is left alone);
 *  3. an opponent has a combo assembled on the battlefield and Forge chose no removal -> the AI casts removal it can cast and
 *     pay for now (destroy, or move from the battlefield) on a piece of it (at instant speed whenever it has priority;
 *     sorcery-speed on its own main phase).
 *  4. (round AI2c, found by round AI2b's 3-player bench) Forge chose to counter an opponent's counterspell that is aimed at a
 *     threat another player cast -> the AI passes and keeps its card: at a table of three, two AIs holding Counterspell fought
 *     each other over the human's Basalt Monolith and it resolved (12 of 15 trials). Protecting its OWN spell is left to Forge.
 * (An earlier rule, "aim Forge's own removal at the biggest threat", was written and taken out before delivery: on the 15 bench
 * boards it never once acted - rule 3 always came first - so it would have shipped untested.)
 * Each forced or changed play is tried at most twice per card, target and turn (a play Forge then fails to pay for is not
 * retried for ever). Everything else is Forge's AI as before. --threat-ai off turns it all off (the AI1 baseline).
 *
 * Every change is logged ("threats: ...", worded so card_check.PROBLEM_LINE never matches it) and sent as {"t":"ai_threat"}.
 */
final class Threats {
    static volatile boolean on = true;                    // --threat-ai off|on
    static final int NONE = 0, CHANGER = 2, NEAR = 2, COMBO = 3;
    static final int CHEAP_RAMP_MAX_MV = 2;
    static final int TRIES = 2;                           // per (AI, turn, move, card, target)

    private static Map<String, List<String>> partners;    // card name (lower case) -> the other card of each listed pair
    private static Set<String> changers;
    private static int pairCount;                         // the pairs read from the combo list
    private static final Map<String, Integer> tries = new HashMap<>();
    private static int triesTurn = -1;

    private Threats() { }

    // ---- the lists ---------------------------------------------------------------------------------------------------------

    static synchronized void load(String listsDir) {
        Map<String, List<String>> p = new HashMap<>();
        Set<String> g = new HashSet<>();
        int count = 0;
        Path dir = Paths.get(listsDir);
        try (BufferedReader r = Files.newBufferedReader(dir.resolve("commander-bracket-combos.txt"), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                String[] parts = line.split("\\|");
                if (line.trim().startsWith("#") || parts.length != 3) {
                    continue;
                }
                String a = parts[1].trim(), b = parts[2].trim();
                count++;
                for (String x : faces(a)) {
                    p.computeIfAbsent(x, k -> new ArrayList<>()).add(b);
                }
                for (String y : faces(b)) {
                    p.computeIfAbsent(y, k -> new ArrayList<>()).add(a);
                }
            }
        } catch (IOException e) {
            System.err.println("threats: no combo list in " + dir + " (" + e.getClass().getSimpleName() + "); combos not judged");
        }
        try (BufferedReader r = Files.newBufferedReader(dir.resolve("gamechangers.txt"), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (!line.trim().isEmpty() && !line.trim().startsWith("#")) {
                    g.addAll(faces(line.trim()));
                }
            }
        } catch (IOException e) {
            System.err.println("threats: no Game Changers list in " + dir + " (" + e.getClass().getSimpleName() + ")");
        }
        partners = p;
        changers = g;
        pairCount = count;
    }

    private static synchronized void ensureLoaded() {
        if (partners == null) {
            load(ForgeConstants.LISTS_DIR);
        }
    }

    /** "A // B" is listed under the whole name and each face; lower case. */
    static List<String> faces(String name) {
        List<String> out = new ArrayList<>();
        String n = name.toLowerCase(Locale.ROOT);
        out.add(n);
        if (n.contains(" // ")) {
            for (String f : n.split(" // ")) {
                out.add(f.trim());
            }
        }
        return out;
    }

    static int comboCount() {
        ensureLoaded();
        return pairCount;
    }

    static int changerCount() {
        ensureLoaded();
        return changers.size();
    }

    // ---- names of cards ----------------------------------------------------------------------------------------------------

    static Set<String> names(Card c) {
        Set<String> out = new HashSet<>();
        if (c == null || c.isFaceDown()) {
            return out;
        }
        out.addAll(faces(c.getName()));
        IPaperCard pc = c.getPaperCard();
        if (pc != null && pc.getName() != null) {
            out.addAll(faces(pc.getName()));
        }
        return out;
    }

    private static boolean isChanger(Card c) {
        for (String n : names(c)) {
            if (changers.contains(n)) {
                return true;
            }
        }
        return false;
    }

    /** True when `c` and some OTHER card in `cards` are a listed pair. */
    private static boolean pairsWith(Card c, Iterable<Card> cards) {
        Set<String> mine = names(c);
        Set<String> wanted = new HashSet<>();
        for (String n : mine) {
            for (String other : partners.getOrDefault(n, Collections.emptyList())) {
                wanted.addAll(faces(other));
            }
        }
        if (wanted.isEmpty()) {
            return false;
        }
        for (Card o : cards) {
            if (o != c && o.getId() != c.getId()) {
                for (String n : names(o)) {
                    if (wanted.contains(n)) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    // ---- how dangerous ----------------------------------------------------------------------------------------------------

    /** A spell being cast by `p`: COMBO when it pairs with a card on p's battlefield, NEAR when it pairs with p's commander in
     *  the command zone, CHANGER when it is a Game Changer, else NONE. */
    static int spellThreat(Card c, Player p) {
        ensureLoaded();
        if (c == null || p == null) {
            return NONE;
        }
        if (pairsWith(c, p.getCardsIn(ZoneType.Battlefield))) {
            return COMBO;
        }
        List<Card> commanders = new ArrayList<>();
        for (Card cz : p.getCardsIn(ZoneType.Command)) {
            if (cz.isCommander()) {
                commanders.add(cz);
            }
        }
        if (pairsWith(c, commanders)) {
            return NEAR;
        }
        return isChanger(c) ? CHANGER : NONE;
    }

    /** A permanent on the battlefield: COMBO when it is a piece of a pair its controller has assembled, CHANGER, else NONE. */
    static int permanentThreat(Card c) {
        ensureLoaded();
        if (c == null || c.getController() == null) {
            return NONE;
        }
        if (pairsWith(c, c.getController().getCardsIn(ZoneType.Battlefield))) {
            return COMBO;
        }
        return isChanger(c) ? CHANGER : NONE;
    }

    static boolean cheapRamp(Card c) {
        return c != null && c.isPermanent() && !c.getManaAbilities().isEmpty() && c.getCMC() <= CHEAP_RAMP_MAX_MV;
    }

    // ---- what kind of play --------------------------------------------------------------------------------------------------

    static boolean isCounter(SpellAbility sa) {
        return sa != null && sa.getApi() == ApiType.Counter && sa.usesTargeting();
    }

    static boolean isRemoval(SpellAbility sa) {
        if (sa == null || !sa.usesTargeting()) {
            return false;
        }
        if (sa.getApi() == ApiType.Destroy) {
            return true;
        }
        if (sa.getApi() == ApiType.ChangeZone) {
            String origin = sa.getParamOrDefault("Origin", "");
            String dest = sa.getParamOrDefault("Destination", "");
            return origin.contains("Battlefield") && !dest.isEmpty() && !dest.contains("Battlefield");
        }
        return false;
    }

    // ---- the decision -------------------------------------------------------------------------------------------------------

    /** Forge's choice for `ai`, kept or changed (see the class comment). Never throws: anything unexpected keeps Forge's choice. */
    static List<SpellAbility> adjust(Player ai, List<SpellAbility> chosen) {
        if (!on || ai == null) {
            return chosen;
        }
        try {
            return decide(ai, chosen);
        } catch (RuntimeException e) {
            System.err.println("threats: left Forge's choice (" + e.getClass().getSimpleName() + " while judging)");
            return chosen;
        }
    }

    private static List<SpellAbility> decide(Player ai, List<SpellAbility> chosen) {
        ensureLoaded();
        Game game = ai.getGame();
        int turn = game.getPhaseHandler().getTurn();
        if (turn != triesTurn) {
            triesTurn = turn;
            tries.clear();
        }
        SpellAbility pick = chosen == null || chosen.isEmpty() ? null : chosen.get(0);
        if (pick != null && pick.isLandAbility()) {
            return chosen;                                               // the land first; asked again right after
        }
        SpellAbility top = game.getStack().isEmpty() ? null : game.getStack().peekAbility();
        boolean theirSpell = top != null && top.isSpell() && top.getActivatingPlayer() != null
                && top.getActivatingPlayer().isOpponentOf(ai) && top.getHostCard() != null;

        if (theirSpell) {
            Card spell = top.getHostCard();
            int threat = spellThreat(spell, top.getActivatingPlayer());
            SpellAbility aimed = isCounter(pick) && pick.getTargets() != null ? pick.getTargets().getFirstTargetedSpell() : null;
            boolean countering = aimed != null && (aimed == top || aimed.getHostCard() == spell
                    || (aimed.getHostCard() != null && aimed.getHostCard().getId() == spell.getId()));
            // 2. don't spend a counterspell (a card) on cheap ramp that is no threat
            if (countering && threat == NONE && cheapRamp(spell) && pick.isSpell()) {
                note(ai, "held", pick, spell, "cheap ramp, no combo, not a Game Changer");
                return null;
            }
            // 4. (round AI2c) don't counter another player's answer to a threat: `top` counters a spell someone other than this
            //    AI cast, and that spell is a threat - countering the counter would let the threat resolve
            if (countering) {
                SpellAbility answered = answeredThreat(ai, top);
                if (answered != null) {
                    note(ai, "held", pick, answered.getHostCard(), "would let " + answered.getActivatingPlayer().getName()
                            + "'s threat resolve");
                    return null;
                }
            }
            // 1. counter a threat Forge let through
            if (threat > NONE && !countering) {
                SpellAbility counter = findCounter(ai, top);
                if (counter != null && tryOnce(ai, turn, "counter", counter, spell)) {
                    note(ai, "countered", counter, spell, why(threat));
                    return single(counter);
                }
            }
            if (countering) {
                return chosen;
            }
        }
        // 3. an assembled combo on an opponent's battlefield, and Forge chose nothing that removes anything
        boolean answering = pick != null && isRemoval(pick);            // Forge's own removal is left as Forge aimed it
        if (!answering && (top == null || theirSpell || (top.getActivatingPlayer() != null && !top.getActivatingPlayer().equals(ai)))) {
            TargetChoices[] before = new TargetChoices[1];
            SpellAbility removal = findRemoval(ai, before);
            if (removal != null) {
                Card target = removal.getTargets().getFirstTargetedCard();
                if (tryOnce(ai, turn, "remove", removal, target)) {
                    note(ai, "removed", removal, target, "a combo piece, assembled");
                    return single(removal);
                }
                restore(removal, before[0]);
            }
        }
        return chosen;
    }

    /** Round AI2c: when `top` is a counter aimed at a spell on the stack that a player other than `ai` cast and that spell is a
     *  threat (spellThreat > NONE), that spell; else null. A counter aimed at the AI's own spell is not this case: protecting its
     *  own spell is Forge's call, as before. */
    static SpellAbility answeredThreat(Player ai, SpellAbility top) {
        if (!isCounter(top) || top.getTargets() == null) {
            return null;
        }
        SpellAbility target = top.getTargets().getFirstTargetedSpell();
        if (target == null || !target.isSpell() || target.getHostCard() == null) {
            return null;
        }
        Player caster = target.getActivatingPlayer();
        if (caster == null || caster.equals(ai)) {
            return null;
        }
        return spellThreat(target.getHostCard(), caster) > NONE ? target : null;
    }

    private static List<SpellAbility> single(SpellAbility sa) {
        List<SpellAbility> l = new ArrayList<>();
        l.add(sa);
        return l;
    }

    private static String why(int threat) {
        return threat == COMBO ? "completes a two-card combo" : threat == CHANGER ? "a Game Changer or one card from a combo with a commander"
                : "no threat";
    }

    private static boolean tryOnce(Player ai, int turn, String move, SpellAbility sa, Card target) {
        Card host = sa.getHostCard();
        String k = ai.getId() + "|" + turn + "|" + move + "|" + (host == null ? -1 : host.getId()) + "|" + (target == null ? -1 : target.getId());
        int n = tries.getOrDefault(k, 0);
        if (n >= TRIES) {
            return false;
        }
        tries.put(k, n + 1);
        return true;
    }

    /** The plays the AI could make now: Forge's own list (hand, battlefield, command zone, and anything it may play from
     *  elsewhere), with each card's alternative costs (Force of Will's pitch cost). */
    private static List<SpellAbility> plays(Player ai) {
        Game game = ai.getGame();
        return ComputerUtilAbility.getOriginalAndAltCostAbilities(
                ComputerUtilAbility.getSpellAbilities(ComputerUtilAbility.getAvailableCards(game, ai), ai), ai);
    }

    /** Targets as they were, so a play that is looked at and not used is left exactly as Forge had it (the same object can
     *  be Forge's own choice for this priority). */
    private static TargetChoices saved(SpellAbility sa) {
        return sa.getTargets() == null ? null : sa.getTargets().clone();
    }

    private static void restore(SpellAbility sa, TargetChoices before) {
        if (before == null) {
            sa.resetTargets();
        } else {
            sa.setTargets(before);
        }
    }

    /** A counter the AI can cast and pay for now, aimed at `top`; null if none. */
    static SpellAbility findCounter(Player ai, SpellAbility top) {
        for (SpellAbility sa : plays(ai)) {
            if (!isCounter(sa)) {
                continue;
            }
            sa.setActivatingPlayer(ai);
            if (!sa.canPlay() || (top.isSpell() && !top.isCounterableBy(sa))) {
                continue;
            }
            TargetChoices before = saved(sa);
            sa.resetTargets();
            if (sa.canTargetSpellAbility(top)) {
                sa.getTargets().add(top);
                if (ComputerUtilCost.canPayCost(sa, ai, false)) {
                    return sa;
                }
            }
            restore(sa, before);
        }
        return null;
    }

    /** Removal the AI can cast and pay for now, already aimed at a piece of an assembled combo; null if none. `before[0]` gets
     *  its targets as they were. */
    static SpellAbility findRemoval(Player ai, TargetChoices[] before) {
        List<Card> pieces = new ArrayList<>();
        for (Player opp : ai.getOpponents()) {
            for (Card c : opp.getCardsIn(ZoneType.Battlefield)) {
                if (permanentThreat(c) == COMBO) {
                    pieces.add(c);
                }
            }
        }
        if (pieces.isEmpty()) {
            return null;
        }
        for (SpellAbility sa : plays(ai)) {
            if (!isRemoval(sa)) {
                continue;
            }
            sa.setActivatingPlayer(ai);
            if (!sa.canPlay()) {
                continue;
            }
            before[0] = saved(sa);
            for (Card piece : pieces) {
                sa.resetTargets();
                if (sa.canTarget(piece)) {
                    sa.getTargets().add(piece);
                    if (ComputerUtilCost.canPayCost(sa, ai, false)) {
                        return sa;
                    }
                }
            }
            restore(sa, before[0]);
        }
        return null;
    }

    // ---- telling ------------------------------------------------------------------------------------------------------------

    /** card_check.PROBLEM_LINE reads any engine-log line with these words as a problem; a card or deck name can hold one. */
    static String quiet(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("Exception", "Excep-tion").replace("ERROR", "ERR-OR").replace("Error", "Err-or")
                .replace("bridge:", "bridge -").replace("Unknown key", "Unknown-key").replace("Could not find", "Could-not find")
                .replace("Unable to", "Unable-to").replace("at forge.", "at forge-");
    }

    private static void note(Player ai, String move, SpellAbility sa, Card target, String why) {
        Card host = sa == null ? null : sa.getHostCard();
        String card = host == null ? "?" : host.getName();
        String tgt = target == null ? "?" : target.getName();
        System.err.println(quiet("threats: " + ai.getName() + " " + move + " " + tgt + " with " + card + " (" + why + ")"));
        Wire w = Checks.wire;
        if (w != null) {
            JsonObject m = new JsonObject();
            m.addProperty("t", "ai_threat");
            m.addProperty("player", ai.getName());
            m.addProperty("move", move);
            m.addProperty("card", card);
            m.addProperty("target", tgt);
            m.addProperty("targetId", target == null ? -1 : target.getId());
            m.addProperty("why", why);
            m.addProperty("turn", ai.getGame().getPhaseHandler().getTurn());
            w.send(m);
        }
    }
}
