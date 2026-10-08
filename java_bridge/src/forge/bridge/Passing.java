// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.LobbyPlayer;
import forge.ai.ComputerUtilMana;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.GameView;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.AbilitySub;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.zone.MagicStack;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.YieldController;
import forge.gui.interfaces.IGuiGame;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.player.LobbyPlayerHuman;
import forge.player.PlayerControllerHuman;
import forge.util.GuiDisplayUtil;

/**
 * Patch 43 (Karl, 5-6 Oct 2026): only the meaningful priority stops. One per seat (each BridgeGui has one).
 *
 * Why: Karl's own games had 685 of 936 commands (73%) be OK, passing priority. Night 12's soak seat got 175 priority stops a
 * game, 247 in a 4-player game. Karl decided, one question at a time (claude/CLICKS_AND_STATS_2026-10-05.md, section 0):
 * <ol>
 *   <li>Pass by itself when you can't do anything: Forge's own "auto-pass when no actions" (APINA), on by default. It no
 *       longer pauses for an opponent's spell you can't answer (RESPECTS_INTERRUPTS off).</li>
 *   <li>Your own spells and activated abilities resolve without a stop. Ctrl held while you cast keeps priority ("hold").</li>
 *   <li>Your own triggers stop you when you can do something (the Thassa's Oracle trigger, then Demonic Consultation).</li>
 *   <li>An opponent's trigger or activated ability stops you only if you hold an answer to it: a card you can play now whose
 *       target can be that kind of stack item (Stifle, Tale's End, Disallow). Plus "always stop on this card", by name.</li>
 *   <li>Default stops (BridgeGui): your Main 1 and Main 2, the opponents' end step.</li>
 *   <li>Keys: Enter = Forge's pass-until-end-of-turn yield (an opponent's spell or an attack on you ends it); Shift+Enter =
 *       pass everything for the rest of this turn ("passturn"); Ctrl = full control for the rest of this turn; Ctrl+Shift = full
 *       control until turned off. Full control: every stop, nothing passed for you.</li>
 * </ol>
 * How: the human seat's controller is {@link Controller}, a PlayerControllerHuman whose chooseSpellAbilityToPlay asks
 * {@link #decide} first. PASS returns null, which Forge's PhaseHandler takes as "I pass" (the same as Forge's own auto-pass and
 * the AI loop guard). STOP clears any running yield and lets Forge prompt. DEFER is Forge exactly as before (phase stops,
 * APINA, "always pass" choices). {@code --classic-stops} keeps the behaviour from before this patch (the card check uses it).
 */
final class Passing {
    /** --classic-stops on the command line: every stop as before patch 43 (old default stops, no passing rules). */
    static volatile boolean classic = false;

    enum Decision { PASS, STOP, DEFER }

    /** The player's wish for Forge's auto-pass-when-nothing-to-do; full control turns it off while it lasts. */
    volatile boolean autoPass = true;
    /** Ctrl+Shift: every stop, nothing passed for me, until turned off. */
    volatile boolean fullControl = false;
    /** Ctrl: full control for the rest of this turn. */
    private volatile int turnControl = -1;
    /** Ctrl held while casting: the next priority with my own item on top is a stop. Used once. */
    volatile boolean holdNext = false;
    /** Shift+Enter: pass everything for the rest of this turn (blocks are still asked). */
    volatile int passTurn = -1;
    /** Card names whose stack items always stop me, however the other rules would decide. */
    final Set<String> alwaysStop = ConcurrentHashMap.newKeySet();
    /** How many priority stops these rules passed this game (the soak and the tests read it). */
    final AtomicInteger passed = new AtomicInteger();
    private volatile int lastNoticed = -1;

    // ---- the decision -------------------------------------------------------------------------------------------------------

    Decision decide(PlayerControllerHuman pc, BridgeGui gui) {
        if (classic) {
            return Decision.DEFER;
        }
        Game game = pc.getGame();
        Player me = pc.getPlayer();
        if (game == null || me == null) {
            return Decision.DEFER;
        }
        int turn = game.getPhaseHandler().getTurn();
        boolean full = fullControlAt(turn);
        syncAutoPass(pc.getYieldController(), full);
        if (full) {
            return Decision.STOP;
        }
        if (passTurn == turn) {
            holdNext = false;
            return Decision.PASS;
        }
        MagicStack stack = game.getStack();
        if (stack.isEmpty()) {
            holdNext = false;                               // the held spell resolved, or the cast was cancelled
            return Decision.DEFER;                          // Forge: phase stops and auto-pass
        }
        SpellAbilityStackInstance top = stack.peek();
        Card src = top.getSourceCard();
        if (src != null && alwaysStop.contains(src.getName())) {
            return Decision.STOP;
        }
        Player owner = top.getActivatingPlayer();
        boolean mine = owner != null && owner.equals(me);
        if (mine) {
            if (holdNext) {                                 // kept until my own item is on top (an opponent's trigger may come first)
                holdNext = false;
                return Decision.STOP;
            }
            return top.isTrigger() ? Decision.DEFER : Decision.PASS;      // my trigger: a stop if I can act (auto-pass decides)
        }
        if (top.isSpell()) {
            return Decision.DEFER;                          // an opponent's spell: a stop whenever I could respond
        }
        if (hasAnswer(me, top)) {
            return Decision.STOP;
        }
        notePassed(gui, top, owner);
        return Decision.PASS;
    }

    boolean fullControlAt(int turn) {
        return fullControl || turnControl == turn;
    }

    boolean fullControlNow(GameView gv) {
        return fullControl || (gv != null && fullControlAt(gv.getTurn()));
    }

    /** Ctrl: full control until this turn ends. */
    void turnControl(GameView gv) {
        if (gv != null) {
            turnControl = gv.getTurn();
        }
    }

    void clearTurnControl() {
        turnControl = -1;
    }

    /** Forge's per-controller preferences (they win over FModel's): auto-pass as the player wants it, off under full control,
     *  and never paused by an opponent's spell I can't answer. */
    void syncAutoPass(YieldController yc, boolean full) {
        if (yc == null) {
            return;
        }
        yc.setPref(FPref.YIELD_AUTO_PASS_NO_ACTIONS, String.valueOf(autoPass && !full && !classic));
        yc.setPref(FPref.YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, String.valueOf(classic));
    }

    // ---- "do I hold an answer to this trigger / ability?" ----------------------------------------------------------------

    private static final ZoneType[] ANSWER_ZONES = {ZoneType.Hand, ZoneType.Battlefield, ZoneType.Command, ZoneType.Flashback};

    /** A card I could play right now whose target can be the stack item on top (Stifle, Tale's End, Disallow, Trickbind ...).
     *  Run under an AI controller, as Forge's own AvailableActions does, so working out a cost never prompts. */
    static boolean hasAnswer(Player me, SpellAbilityStackInstance top) {
        SpellAbility target = top.getSpellAbility();
        boolean trigger = top.isTrigger();
        AtomicBoolean found = new AtomicBoolean(false);
        LobbyPlayer lp = me.getOriginalLobbyPlayer();
        me.runWithController(() -> found.set(scanForAnswer(me, target, trigger)),
                new PlayerControllerAi(me.getGame(), me, lp));
        return found.get();
    }

    private static boolean scanForAnswer(Player me, SpellAbility target, boolean trigger) {
        for (ZoneType z : ANSWER_ZONES) {
            for (Card c : me.getCardsIn(z)) {
                List<SpellAbility> abilities;
                try {
                    abilities = c.getAllPossibleAbilities(me, true);
                } catch (RuntimeException e) {
                    continue;
                }
                for (SpellAbility sa : abilities) {
                    if (sa == null || sa.isManaAbility() || !answers(sa, target, trigger, 0)) {
                        continue;
                    }
                    if (affordable(sa, me)) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    static boolean answers(SpellAbility sa, SpellAbility target, boolean trigger, int depth) {
        if (sa == null || depth > 6) {
            return false;
        }
        for (SpellAbility s = sa; s != null; s = s.getSubAbility()) {
            if (targetsStackItem(s, target, trigger)) {
                return true;
            }
        }
        List<AbilitySub> choices = sa.getAdditionalAbilityList("Choices");      // modal spells ("choose one")
        if (choices != null) {
            for (AbilitySub ch : choices) {
                if (answers(ch, target, trigger, depth + 1)) {
                    return true;
                }
            }
        }
        return false;
    }

    /** Forge marks what a stack-targeting effect may target with TargetType: "Spell", "Activated", "Triggered" (with
     *  restrictions after a dot). Counterspell says Spell, so it is no answer to a trigger; Stifle says Activated,Triggered. */
    static boolean targetsStackItem(SpellAbility s, SpellAbility target, boolean trigger) {
        if (!s.usesTargeting() || !s.hasParam("TargetType")) {
            return false;
        }
        String want = trigger ? "Triggered" : "Activated";
        boolean kind = false;
        for (String t : s.getParam("TargetType").split(",")) {
            if (t.trim().startsWith(want)) {
                kind = true;
                break;
            }
        }
        if (!kind) {
            return false;
        }
        if (target == null) {
            return true;
        }
        try {
            return s.canTargetSpellAbility(target);
        } catch (RuntimeException e) {
            return true;                                    // can't tell: count it, a stop is the safe side
        }
    }

    private static boolean affordable(SpellAbility sa, Player me) {
        try {
            if (sa.getPayCosts() == null || !sa.getPayCosts().hasManaCost()) {
                return true;
            }
            return ComputerUtilMana.canPayManaCost(sa, me, 0, false);
        } catch (RuntimeException e) {
            return true;
        }
    }

    // ---- what the client is told ----------------------------------------------------------------------------------------

    /** One line per opponent trigger / ability passed for me: {"t":"passed", "card", "cardId", "player", "kind"}. */
    private void notePassed(BridgeGui gui, SpellAbilityStackInstance top, Player owner) {
        passed.incrementAndGet();
        int id = top.getId();
        if (id == lastNoticed || gui == null) {
            return;
        }
        lastNoticed = id;
        Card src = top.getSourceCard();
        JsonObject m = new JsonObject();
        m.addProperty("t", "passed");
        m.addProperty("card", src == null ? "?" : src.getName());
        m.addProperty("cardId", src == null ? -1 : src.getId());
        m.addProperty("player", owner == null ? "?" : owner.getName());
        m.addProperty("kind", top.isTrigger() ? "trigger" : "ability");
        try {
            gui.wire.send(m);
        } catch (RuntimeException e) {
            // the line is only information
        }
    }

    /** For the snapshot's "yield" object: what these rules are doing for me. */
    JsonObject toJson(GameView gv) {
        JsonObject o = new JsonObject();
        o.addProperty("classic", classic);
        o.addProperty("fullControl", fullControl);
        o.addProperty("turnControl", !fullControl && fullControlNow(gv));
        o.addProperty("passTurn", gv != null && passTurn == gv.getTurn());
        o.addProperty("hold", holdNext);
        o.addProperty("passed", passed.get());
        JsonArray names = new JsonArray();
        for (String n : alwaysStop) {
            names.add(n);
        }
        o.add("alwaysStop", names);
        return o;
    }

    /** The Passing of the seat this controller plays at, or null (a GUI that isn't the bridge's). */
    static Passing of(IGuiGame gui) {
        return gui instanceof BridgeGui bg ? bg.passing : null;
    }

    // ---- the seat ----------------------------------------------------------------------------------------------------------

    /** LobbyPlayerHuman whose in-game controller is {@link Controller}. Forge's createIngamePlayer is repeated (it is short). */
    static final class Human extends LobbyPlayerHuman {
        Human(String name) {
            super(name);
        }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player player = new Player(GuiDisplayUtil.personalizeHuman(getName()), game, id);
            player.setFirstController(new Controller(game, player, this));
            return player;
        }
    }

    static final class Controller extends PlayerControllerHuman {
        Controller(Game game, Player p, LobbyPlayer lp) {
            super(game, p, lp);
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            IGuiGame g = getGui();
            Passing p = of(g);
            if (p == null || getGame() == null || getGame().isGameOver()) {
                return super.chooseSpellAbilityToPlay();
            }
            Decision d;
            try {
                d = p.decide(this, (BridgeGui) g);
            } catch (RuntimeException e) {
                System.err.println("bridge: passing rules failed, Forge decides: " + e);
                d = Decision.DEFER;
            }
            if (d == Decision.PASS) {
                g.awaitNextInput();
                return null;                                // Forge's PhaseHandler: null = "I pass"
            }
            if (d == Decision.STOP) {
                YieldController yc = getYieldController();
                if (yc != null && yc.isYieldActive()) {
                    yc.clearActiveYieldAndDispatch();
                }
            }
            return super.chooseSpellAbilityToPlay();
        }
    }

    /** For tests: the controller class the bridge gives a human seat. */
    static Class<? extends PlayerController> controllerClass() {
        return Controller.class;
    }
}
