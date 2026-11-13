package forge.bridge;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.apache.commons.lang3.tuple.ImmutablePair;

import com.google.gson.JsonObject;

import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.ability.AbilityKey;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.SpellAbility;
import forge.game.trigger.Trigger;
import forge.game.trigger.WrappedAbility;
import forge.game.zone.ZoneType;

/**
 * Round 28e: stops Forge's AI from repeating one ability forever.
 *
 * Soak nights 7 and 9: the Kinnan AI had Kinnan, Bonder Prodigy, Grim Monolith and a Copy Artifact copying it. Each Monolith
 * taps for {C}{C}{C}, Kinnan adds one more, and 4 is exactly the other Monolith's untap cost, so the AI untapped one with the
 * other, back and forth, 6,530 and 7,134 times, gaining nothing, until the soak's 30-minute limit ended the game. Forge's AI
 * judges each activation on its own and never sees that it is going round in circles. For a person at the table it is a
 * frozen game.
 *
 * The guard: an AI player may use the same ability of the same card at most {@link #cap} times in one turn. After that,
 * when the AI picks that ability again, it passes priority instead (Forge's PhaseHandler treats a null choice as "I pass"),
 * and the bridge says so once per card and turn ({"t":"ai_loop_guard"}). Other abilities, and the same ability next turn,
 * are not affected. Playing a land is never counted. The default of 10 is well above anything the AI does on purpose (the
 * most Basalt Monolith untaps in one turn in 64 evaluation games was 4) and low because every repeat goes on the stack, so a
 * person at the table may have to pass priority for each one. A scripted combo line (BRIEF_AI_COMBO_LINES) would have to be
 * exempted from it. Checked live (round 28e): Kinnan + two Grim Monoliths, one tapped, in the other player's end step -
 * guard off: 491 untaps in 60 seconds and the turn never ended; guard on: it stops at the limit and the game moves on.
 *
 * Round LG2 (Karl, 13 Oct: "count only when the board isn't shrinking"): a repeat counts only when the AI's own board - the
 * permanents it controls, and the creatures among them - is no smaller than at that ability's previous use. Soak night 14's six
 * stops were Carrion Feeder eating real creatures and tokens for an aristocrats drain: each use costs a creature, so the chain
 * ends by itself, and the guard was cutting the AI's clock at 10. A board that shrinks on every use can't go on for ever; the
 * Monolith pair gives nothing up and is still stopped at 10. To be safe against a chain the count can't see through (one that
 * shrinks and regrows the board), every use of the ability is also counted, and {@link #SHRINK_CAP} uses in one turn stop it too.
 *
 * Round ENG-FELIDAR (3 Nov 2026): optional triggers. Online draft soak 1, draft 13 (claude/CD5_2026-10-27.md): Felidar Guardian
 * and Restoration Angel each have "When this enters, you may exile [the other], then return it" - an optional ("may") trigger
 * that Forge's AI always accepts, so the two flickered each other 4,614 times until Java ran out of memory. No ability was
 * activated, so the count above never saw it. Forge asks the AI at the trigger's resolution, through
 * PlayerControllerAi.confirmTrigger(WrappedAbility) (WrappedAbility.resolve, for a trigger with OptionalDecider$; checked with
 * javap on Forge 9a6f776). The guard now also counts each "yes" per (source card, trigger, triggering card) per turn and answers
 * "no" at {@link #cap} - the same "bridge: loop guard: ..." line and {"t":"ai_loop_guard"} (with "kind": "trigger"). The
 * triggering card is part of the key so that one card that triggers on many different cards (Mentor of the Meek and a
 * token swarm: one yes per new creature) is not cut at 10; every yes of that card and trigger is also counted, and
 * {@link #SHRINK_CAP} in one turn stops it too, in case a loop makes a new triggering object every time. A mandatory trigger
 * can't be refused: it is not counted here.
 */
final class LoopGuard {
    static final int DEFAULT_CAP = 10;
    static int cap = DEFAULT_CAP;                  // --loop-cap N on the command line; 0 turns the guard off
    static final int SHRINK_CAP = 100;             // Round LG2: every use of one ability in one turn, shrinking or not

    private LoopGuard() { }

    /** The AI opponent as Forge's GamePlayerUtil.createAiPlayer made it, with the same name, profile and avatar, but guarded. */
    static LobbyPlayer guarded(LobbyPlayer made) {
        if (!(made instanceof LobbyPlayerAi) || (cap <= 0 && !Threats.on)) {     // Round AI2: Threats rides on this controller
            return made;
        }
        LobbyPlayerAi src = (LobbyPlayerAi) made;
        Lobby g = new Lobby(src.getName());
        g.setAiProfile(src.getAiProfile());
        g.setAvatarIndex(src.getAvatarIndex());
        g.setSleeveIndex(src.getSleeveIndex());
        return g;
    }

    /** LobbyPlayerAi whose in-game controller is {@link Controller}. Forge's own createControllerFor is private, so it is
     *  repeated here: a PlayerControllerAi with no simulation option (createAiPlayer passes no options either). */
    static final class Lobby extends LobbyPlayerAi {
        Lobby(String name) {
            super(name, null);
        }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player ai = new Player(getName(), game, id);
            ai.setFirstController(new Controller(game, ai, this));
            return ai;
        }

        @Override
        public PlayerController createMindSlaveController(Player master, Player slave) {
            return new Controller(slave.getGame(), slave, this);
        }
    }

    static final class Controller extends PlayerControllerAi {
        private int turn = -1;
        private final Map<String, Integer> uses = new HashMap<>();      // the repeats that count (the board didn't shrink)
        private final Map<String, Integer> all = new HashMap<>();       // Round LG2: every use
        private final Map<String, int[]> board = new HashMap<>();       // Round LG2: the AI's board at the ability's last use
        private final Set<String> told = new HashSet<>();
        private final Map<String, Integer> yes = new HashMap<>();       // Round ENG-FELIDAR: accepted optional triggers
        private final Map<String, Integer> yesAll = new HashMap<>();    // ... per source card and trigger, any triggering card

        Controller(Game game, Player p, LobbyPlayer lp) {
            super(game, p, lp);
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            long t0 = Timing.start();
            List<SpellAbility> chosen = Threats.adjust(getPlayer(), super.chooseSpellAbilityToPlay());   // Round AI2
            Timing.mark("ai", t0);                                      // round PERF1: one AI decision
            if (chosen == null || chosen.isEmpty() || cap <= 0) {
                return chosen;
            }
            newTurn();
            for (SpellAbility sa : chosen) {
                if (sa == null || sa.isLandAbility()) {
                    continue;
                }
                String k = key(sa);
                if (uses.getOrDefault(k, 0) >= cap || all.getOrDefault(k, 0) >= SHRINK_CAP) {
                    tell(sa, k);
                    return null;                       // pass priority instead
                }
            }
            int[] here = boardOf(getPlayer());
            for (SpellAbility sa : chosen) {
                if (sa != null && !sa.isLandAbility()) {
                    String k = key(sa);
                    if (!shrank(board.get(k), here)) {
                        uses.merge(k, 1, Integer::sum);
                    }
                    all.merge(k, 1, Integer::sum);
                    board.put(k, here);
                }
            }
            return chosen;
        }

        /** A new turn starts every count again. */
        private void newTurn() {
            int now = getGame().getPhaseHandler().getTurn();
            if (now != turn) {
                turn = now;
                uses.clear();
                all.clear();
                board.clear();
                told.clear();
                yes.clear();
                yesAll.clear();
            }
        }

        /** Round ENG-FELIDAR: the AI's "yes" to an optional trigger, counted per turn; "no" at the cap. */
        @Override
        public boolean confirmTrigger(WrappedAbility wa) {
            boolean ok = super.confirmTrigger(wa);
            if (!ok || cap <= 0 || wa == null || wa.isMandatory()) {
                return ok;
            }
            newTurn();
            String k = triggerKey(wa);
            String kAll = k.substring(0, k.lastIndexOf('|'));
            if (yes.getOrDefault(k, 0) >= cap || yesAll.getOrDefault(kAll, 0) >= SHRINK_CAP) {
                tellTrigger(wa, k, kAll);
                return false;                          // decline the "may"
            }
            yes.merge(k, 1, Integer::sum);
            yesAll.merge(kAll, 1, Integer::sum);
            return true;
        }

        int triggerYes(WrappedAbility wa) {
            return yes.getOrDefault(triggerKey(wa), 0);
        }

        /** "T|source id|trigger text|triggering card id" (-1 when the trigger has no triggering card). */
        static String triggerKey(WrappedAbility wa) {
            Card host = wa.getHostCard();
            Object by = wa.getTriggeringObject(AbilityKey.Card);
            int byId = by instanceof Card ? ((Card) by).getId() : -1;
            return "T|" + (host == null ? -1 : host.getId()) + "|" + triggerText(wa) + "|" + byId;
        }

        static String triggerText(WrappedAbility wa) {
            Trigger t = wa.getTrigger();
            String d = t != null && t.hasParam("TriggerDescription") ? t.getParam("TriggerDescription") : null;
            if (d == null) {
                SpellAbility in = wa.getWrappedAbility();
                d = in == null ? wa.getDescription() : in.getDescription();
            }
            Card host = wa.getHostCard();
            return d == null ? "" : (host == null ? d : d.replace("CARDNAME", host.getName()));
        }

        private void tellTrigger(WrappedAbility wa, String k, String kAll) {
            if (!told.add(k)) {
                return;
            }
            Card host = wa.getHostCard();
            String card = host == null ? "?" : host.getName();
            boolean any = yes.getOrDefault(k, 0) < cap;              // stopped by SHRINK_CAP over many triggering cards
            int count = any ? yesAll.getOrDefault(kAll, 0) : cap;
            String text = triggerText(wa);
            System.err.println("bridge: loop guard: " + getPlayer().getName() + " said yes to " + card + "'s trigger (" + text
                    + ") " + count + " times this turn" + (any ? " (on different cards)" : "") + "; answering no instead");
            if (Checks.wire != null) {
                JsonObject m = new JsonObject();
                m.addProperty("t", "ai_loop_guard");
                m.addProperty("kind", "trigger");
                m.addProperty("player", getPlayer().getName());
                m.addProperty("card", card);
                m.addProperty("cardId", host == null ? -1 : host.getId());
                m.addProperty("ability", text);
                m.addProperty("turn", turn);
                m.addProperty("count", count);
                m.addProperty("uses", yesAll.getOrDefault(kAll, 0));
                m.addProperty("shrinking", false);
                Checks.wire.send(m);
            }
        }

        /** bug-1568367922328510465: surveil keeps on top only what the AI can pay life for (see SurveilGuard). */
        @Override
        public ImmutablePair<CardCollection, CardCollection> arrangeForSurveil(CardCollection topN) {
            return SurveilGuard.limit(getPlayer(), super.arrangeForSurveil(topN));
        }

        /** Round AI-ATTACK: the attack declaration with a time budget (see AttackCache.declareWithBudget). */
        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            AttackCache.declareWithBudget(attacker, combat, () -> super.declareAttackers(attacker, combat));
        }

        int uses(SpellAbility sa) {
            return uses.getOrDefault(key(sa), 0);
        }

        int allUses(SpellAbility sa) {
            return all.getOrDefault(key(sa), 0);
        }

        /** Round LG2: the permanents this player controls, and the creatures among them. */
        static int[] boardOf(Player p) {
            int permanents = 0;
            int creatures = 0;
            for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
                permanents++;
                if (c.isCreature()) {
                    creatures++;
                }
            }
            return new int[] {permanents, creatures};
        }

        /** Round LG2: smaller than at the last use - fewer permanents or fewer creatures. The first use never shrank. */
        static boolean shrank(int[] before, int[] now) {
            return before != null && (now[0] < before[0] || now[1] < before[1]);
        }

        private static String key(SpellAbility sa) {
            Card host = sa.getHostCard();
            return (host == null ? -1 : host.getId()) + "|" + sa.getDescription();
        }

        private void tell(SpellAbility sa, String k) {
            if (!told.add(k)) {
                return;
            }
            Card host = sa.getHostCard();
            String card = host == null ? "?" : host.getName();
            boolean shrinking = uses.getOrDefault(k, 0) < cap;      // stopped by SHRINK_CAP, not by the repeats
            int count = shrinking ? all.getOrDefault(k, 0) : cap;
            System.err.println("bridge: loop guard: " + getPlayer().getName() + " used " + card + " (" + sa.getDescription()
                    + ") " + count + " times this turn" + (shrinking ? " (the board kept shrinking)" : "") + "; passing instead");
            if (Checks.wire != null) {
                JsonObject m = new JsonObject();
                m.addProperty("t", "ai_loop_guard");
                m.addProperty("kind", "ability");
                m.addProperty("player", getPlayer().getName());
                m.addProperty("card", card);
                m.addProperty("cardId", host == null ? -1 : host.getId());
                m.addProperty("ability", sa.getDescription());
                m.addProperty("turn", turn);
                m.addProperty("count", count);
                m.addProperty("uses", all.getOrDefault(k, 0));           // Round LG2: every use, shrinking or not
                m.addProperty("shrinking", shrinking);
                Checks.wire.send(m);
            }
        }
    }
}
