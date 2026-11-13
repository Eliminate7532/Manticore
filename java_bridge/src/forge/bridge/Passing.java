// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

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

    enum Decision { PASS, STOP, DEFER, SKIP }

    /**
     * Round PERF1 (Karl, 3 Nov 2026: "waiting after I act" on a big board). {@code --skip-scan on|off|compare}.
     *
     * <p>At a priority in a step this seat doesn't stop at (the turn bar's dots: in Fast, every step but my Main 1 and Main 2
     * and the opponents' end steps), with an empty stack, Forge always passes for me: its chooseSpellAbilityToPlay passes
     * whenever skipsPromptForStackOrPhase() is true, whatever the player could do. But it first runs its "can this player act?"
     * scan (AvailableActions.collectActionable: every card in my hand, battlefield and flashback zone, its abilities, their
     * costs against all my mana and their targets against the whole board) - with the actionable-card highlights on, it walks
     * every card, with no early exit, under a time budget of up to 1.5 s. On Karl's Surface that budget ran out
     * ("AvailableActions: heuristic timed out after 1500ms") and the bench (tools/bench_board.py) shows the scan is most of
     * what Forge does for me at a priority. "on" (the default) passes at such a priority without the scan: SKIP is handled
     * like PASS (awaitNextInput, then null = "I pass"), the same thing Forge's own code does after the scan.
     *
     * <p>Only when the scan's result can't matter: the stack is empty, the step is set to be skipped for this seat, Forge's
     * "auto-pass when I can't act" is on for this seat (Fast, not full control: then Forge's InputPassPriority shows the
     * normal prompt whatever the yield-ended flag says), no yield is running (Forge's own yield code then decides, as before)
     * and no macro. What the skip leaves out, and why that is safe: the scan's set (cachedActionableCards) is read only when
     * a priority prompt is shown, and every prompt runs its own scan first; PlayerView.hasAvailableActions is read by
     * mayAutoPass and the prompt's suggestions, both again only after a fresh scan; YieldController.noteMayAutoPassResult is
     * not called for the skipped priority - its yield-ended flag is read only by InputPassPriority while auto-pass is off.
     *
     * <p>"compare": the skip is never taken; Forge runs as before and each time the skip would have been taken, the bridge
     * checks Forge passed without asking. A difference is a "bridge: passscan compare DIFFERENCE" line in forge_engine.log
     * (the soak's rules then report it); "passscan: compare" lines count the agreements. "off": Forge exactly as before.
     */
    static volatile String skipScan = "on";
    static final List<String> SKIP_MODES = List.of("on", "off", "compare");
    /** Tests only (needs --dev): pretend Forge asked at every Nth compared priority, which compare mode must report. */
    static volatile int comparePlant = 0;
    static final AtomicInteger compareSame = new AtomicInteger();
    static final AtomicInteger compareDiffer = new AtomicInteger();

    /**
     * Round PERF2 (Karl, 4 Nov 2026: "Forge's auto-pass pauses", PERF1's "found, your call"). {@code {"c":"pauses","mode":...}}
     * per seat, {@code --pauses MODE} for every human seat at start (tests, the soak). When Forge passes priority for me by itself
     * (its own auto-pass, a yield, an auto-yielded ability), PlayerControllerHuman.chooseSpellAbilityToPlay first sleeps "so the
     * game doesn't jump ahead": 400 ms (FControlGamePlayback.resolveDelay) with something on the stack, 200 ms (phasesDelay) with
     * an empty stack at a step this seat stops at. Its two per-controller preferences YIELD_SKIP_RESOLVE_DELAY and
     * YIELD_SKIP_PHASE_DELAY switch them off. "normal" = Forge as it is (both false, today's behaviour, the default); "off" = both
     * true; "short" = both true and the bridge sleeps half of what Forge would have, at the same moment (BridgeGui.awaitNextInput,
     * which Forge calls right after its sleep). Patch 43's own passes (Decision.PASS / SKIP) never had a pause and still don't.
     */
    static final List<String> PAUSE_MODES = List.of("normal", "short", "off");
    static volatile String defaultPauses = "normal";
    /** This seat's mode; null = the command line's (defaultPauses). */
    volatile String pauses = null;
    /** Tests only (needs --dev): 1 = Off still pauses; 2 = Short sleeps Forge's whole pause. */
    static volatile int pausePlant = 0;
    /** The pause Forge would make if its chooseSpellAbilityToPlay, running now on this thread, passes by itself; null outside. */
    static final ThreadLocal<int[]> PENDING = new ThreadLocal<>();
    /** This game: how often Forge passed for me with a pause due (by kind), and the ms actually waited (Forge's or ours). */
    final AtomicInteger phasePauses = new AtomicInteger();
    final AtomicInteger resolvePauses = new AtomicInteger();
    final AtomicLong pausedMs = new AtomicLong();

    String pauseMode() {
        String m = pauses;
        return m != null ? m : defaultPauses;
    }

    /** Forge 9a6f776's own rule (PlayerControllerHuman.chooseSpellAbilityToPlay), without its two preferences. */
    static int forgeDelay(PlayerControllerHuman pc) {
        Game g = pc.getGame();
        if (g == null) {
            return 0;
        }
        if (!g.getStack().isEmpty()) {
            return forge.gui.control.FControlGamePlayback.resolveDelay;
        }
        Player turn = g.getPhaseHandler().getPlayerTurn();
        if (turn == null || pc.isUiSetToSkipPhase(turn.getView(), g.getPhaseHandler().getPhase())) {
            return 0;
        }
        return forge.gui.control.FControlGamePlayback.phasesDelay;
    }

    /** Called by BridgeGui.awaitNextInput: if it is Forge's auto-pass inside this seat's chooseSpellAbilityToPlay, count the
     *  pause and, in Short, make it (half). Never holds a lock while sleeping. */
    void notePause() {
        int[] p = PENDING.get();
        if (p == null || p[0] <= 0) {
            return;
        }
        int delay = p[0];
        p[0] = 0;                                           // once per priority
        if (delay >= forge.gui.control.FControlGamePlayback.resolveDelay) {
            resolvePauses.incrementAndGet();
        } else {
            phasePauses.incrementAndGet();
        }
        String m = pauseMode();
        long wait;
        if ("normal".equals(m)) {
            pausedMs.addAndGet(delay);                      // Forge has just slept it
            return;
        } else if ("short".equals(m)) {
            wait = pausePlant == 2 ? delay : delay / 2;
        } else {
            wait = pausePlant == 1 ? delay : 0;
        }
        if (wait > 0) {
            try {
                Thread.sleep(wait);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            pausedMs.addAndGet(wait);
        }
    }

    JsonObject pausesJson() {
        JsonObject o = new JsonObject();
        o.addProperty("mode", pauseMode());
        o.addProperty("phase", phasePauses.get());
        o.addProperty("resolve", resolvePauses.get());
        o.addProperty("ms", pausedMs.get());
        return o;
    }

    /** Round PRI1 (Karl, 9 Oct 2026: "Slow"): this seat plays with the stops from before patch 43 - the old default stops
     *  (BridgeGui.slowMine / slowTheirs), nothing passed for me, auto-pass off - what --classic-stops does for the whole engine,
     *  but for one seat and switchable mid-game ({"c":"speed","mode":"slow"|"fast"}). "Fast" (false) is patch 43's rules. */
    volatile boolean slow = false;

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
    /** Round PERF1 (--timing): when Forge's own chooseSpellAbilityToPlay started for this seat, 0 when not running. Ended by
     *  BridgeGui.noteInput (Forge showed a question) or by the method returning (Forge passed by itself). */
    volatile long forgeT0 = 0L;

    void forgeEnded(String how) {
        long t0 = forgeT0;
        if (t0 == 0L) {
            return;
        }
        forgeT0 = 0L;
        JsonObject o = Timing.line("forge", Timing.since(t0));
        o.addProperty("ret", how);
        Timing.send(o);
    }

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
        if (slow) {
            holdNext = false;
            return Decision.DEFER;                          // round PRI1: Forge as before patch 43 (auto-pass is off: see syncAutoPass)
        }
        if (passTurn == turn) {
            holdNext = false;
            return Decision.PASS;
        }
        MagicStack stack = game.getStack();
        if (stack.isEmpty()) {
            holdNext = false;                               // the held spell resolved, or the cast was cancelled
            if (!"off".equals(skipScan) && skippedStep(pc, gui)) {
                return Decision.SKIP;                       // round PERF1: Forge would pass here whatever its scan found
            }
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
        // bug-1567760778684272653 (Karl, 7 Nov 2026: "Only when I can answer"): an opponent's spell stops me only when I hold
        // something that can target it (a counter, a redirect), the way triggers and abilities already work. Before, it was
        // DEFER: Forge's auto-pass stopped me whenever I could do anything at all, at every spell of every opponent in a pod.
        long ta = Timing.start();
        boolean answer = hasAnswer(me, top);
        Timing.mark("answer", ta);                          // round PERF1
        if (answer) {
            return Decision.STOP;
        }
        notePassed(gui, top, owner);
        return Decision.PASS;
    }

    /** Round PERF1: Forge's own skipsPromptForStackOrPhase() for an empty stack (it reads the view too), plus the conditions
     *  under which leaving out its scan changes nothing (see {@link #skipScan}). Called after full control and Slow returned. */
    boolean skippedStep(PlayerControllerHuman pc, BridgeGui gui) {
        if (gui == null || !autoPass || pc.isMacroActive()) {
            return false;
        }
        YieldController yc = pc.getYieldController();
        if (yc == null || yc.isYieldActive()) {
            return false;
        }
        GameView gv = gui.getGameView();
        if (gv == null || gv.peekStack() != null) {
            return false;
        }
        forge.game.player.PlayerView turn = gv.getPlayerTurn();
        forge.game.phase.PhaseType phase = gv.getPhase();
        return turn != null && phase != null && gui.isUiSetToSkipPhase(turn, phase);
    }

    boolean fullControlAt(int turn) {
        return fullControl || turnControl == turn;
    }

    /** Round PRI1: this seat plays the way it did before patch 43 (--classic-stops for the whole engine, or Slow for this seat). */
    boolean classicNow() {
        return classic || slow;
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
     *  and never paused by an opponent's spell I can't answer. Round PRI1: off in Slow too (the player's wish is kept, so Fast
     *  gives it back), and an interrupt pauses a skip again, as before patch 43. */
    void syncAutoPass(YieldController yc, boolean full) {
        if (yc == null) {
            return;
        }
        yc.setPref(FPref.YIELD_AUTO_PASS_NO_ACTIONS, String.valueOf(autoPass && !full && !classicNow()));
        yc.setPref(FPref.YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, String.valueOf(classicNow()));
        boolean skip = !"normal".equals(pauseMode());       // round PERF2: Short makes its own (half) pause, Off none
        yc.setPref(FPref.YIELD_SKIP_PHASE_DELAY, String.valueOf(skip));
        yc.setPref(FPref.YIELD_SKIP_RESOLVE_DELAY, String.valueOf(skip));
    }

    // ---- "do I hold an answer to this spell / trigger / ability?" --------------------------------------------------------

    private static final ZoneType[] ANSWER_ZONES = {ZoneType.Hand, ZoneType.Battlefield, ZoneType.Command, ZoneType.Flashback};

    /** A card I could play right now whose target can be the stack item on top (Stifle, Tale's End, Disallow, Trickbind ...).
     *  Run under an AI controller, as Forge's own AvailableActions does, so working out a cost never prompts. */
    static boolean hasAnswer(Player me, SpellAbilityStackInstance top) {
        SpellAbility target = top.getSpellAbility();
        String want = stackKind(top);
        AtomicBoolean found = new AtomicBoolean(false);
        LobbyPlayer lp = me.getOriginalLobbyPlayer();
        me.runWithController(() -> found.set(scanForAnswer(me, target, want)),
                new PlayerControllerAi(me.getGame(), me, lp));
        return found.get();
    }

    /** Forge's TargetType word for the kind of stack item on top: "Spell", "Triggered" or "Activated". */
    static String stackKind(SpellAbilityStackInstance top) {
        return top.isSpell() ? "Spell" : top.isTrigger() ? "Triggered" : "Activated";
    }

    private static boolean scanForAnswer(Player me, SpellAbility target, String want) {
        for (ZoneType z : ANSWER_ZONES) {
            for (Card c : me.getCardsIn(z)) {
                List<SpellAbility> abilities;
                try {
                    abilities = c.getAllPossibleAbilities(me, true);
                } catch (RuntimeException e) {
                    continue;
                }
                for (SpellAbility sa : abilities) {
                    if (sa == null || sa.isManaAbility() || !answers(sa, target, want, 0)) {
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

    static boolean answers(SpellAbility sa, SpellAbility target, String want, int depth) {
        if (sa == null || depth > 6) {
            return false;
        }
        for (SpellAbility s = sa; s != null; s = s.getSubAbility()) {
            if (targetsStackItem(s, target, want)) {
                return true;
            }
        }
        List<AbilitySub> choices = sa.getAdditionalAbilityList("Choices");      // modal spells ("choose one")
        if (choices != null) {
            for (AbilitySub ch : choices) {
                if (answers(ch, target, want, depth + 1)) {
                    return true;
                }
            }
        }
        return false;
    }

    /** Forge marks what a stack-targeting effect may target with TargetType: "Spell", "Activated", "Triggered" (with
     *  restrictions after a dot). Counterspell says Spell, so it is no answer to a trigger; Stifle says Activated,Triggered, so it
     *  is no answer to a spell. */
    static boolean targetsStackItem(SpellAbility s, SpellAbility target, String want) {
        if (!s.usesTargeting() || !s.hasParam("TargetType")) {
            return false;
        }
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

    /** One line per opponent spell / trigger / ability passed for me: {"t":"passed", "card", "cardId", "player", "kind"}. */
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
        m.addProperty("kind", top.isSpell() ? "spell" : top.isTrigger() ? "trigger" : "ability");
        try {
            gui.wire.send(m);
        } catch (RuntimeException e) {
            // the line is only information
        }
    }

    /** For the snapshot's "yield" object: what these rules are doing for me. */
    JsonObject toJson(GameView gv) {
        JsonObject o = new JsonObject();
        o.addProperty("classic", classicNow());          // round PRI1: true in Slow too - the table then plays the old way
        o.addProperty("slow", slow);                     // round PRI1: Karl's Speed setting reached this seat
        o.addProperty("fullControl", fullControl);
        o.addProperty("turnControl", !fullControl && fullControlNow(gv));
        o.addProperty("passTurn", gv != null && passTurn == gv.getTurn());
        o.addProperty("hold", holdNext);
        o.addProperty("passed", passed.get());
        o.addProperty("pauses", pauseMode());            // round PERF2: Karl's Pauses setting reached this seat
        o.add("paused", pausesJson());
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
            long td = Timing.start();
            try {
                d = p.decide(this, (BridgeGui) g);
            } catch (RuntimeException e) {
                System.err.println("bridge: passing rules failed, Forge decides: " + e);
                d = Decision.DEFER;
            }
            if (Timing.on) {                                // round PERF1
                JsonObject t = Timing.line("decide", Timing.since(td));
                t.addProperty("d", d.name());
                Timing.send(t);
            }
            if (d == Decision.SKIP && "compare".equals(skipScan)) {
                return compareSkip(p, (BridgeGui) g);
            }
            if (d == Decision.PASS || d == Decision.SKIP) {
                g.awaitNextInput();
                return null;                                // Forge's PhaseHandler: null = "I pass"
            }
            if (d == Decision.STOP) {
                YieldController yc = getYieldController();
                if (yc != null && yc.isYieldActive()) {
                    yc.clearActiveYieldAndDispatch();
                }
            }
            return forgeChooses(p);
        }

        /** Forge's own chooseSpellAbilityToPlay (timed with --timing). */
        private List<SpellAbility> forgeChooses(Passing p) {
            PENDING.set(new int[] {forgeDelay(this)});      // round PERF2: counted / made by BridgeGui.awaitNextInput
            try {
                if (!Timing.on) {
                    return super.chooseSpellAbilityToPlay();
                }
                p.forgeT0 = System.nanoTime();
                List<SpellAbility> r = super.chooseSpellAbilityToPlay();
                p.forgeEnded("pass");                       // still running = Forge passed without asking (auto-pass, skipped step)
                return r;
            } finally {
                PENDING.remove();
            }
        }

        /** Round PERF1, --skip-scan compare: Forge's own way, checked against what the skip would have done - pass, nothing
         *  asked. Returns Forge's answer, so the game goes on exactly as with "off". */
        private List<SpellAbility> compareSkip(Passing p, BridgeGui g) {
            Game game = getGame();
            int turn = game.getPhaseHandler().getTurn();
            String phase = String.valueOf(game.getPhaseHandler().getPhase());
            long seq = g.inputSeq();
            List<SpellAbility> r = forgeChooses(p);
            boolean asked = g.inputSeq() != seq;
            int n = compareSame.get() + compareDiffer.get() + 1;
            if (comparePlant > 0 && n % comparePlant == 0) {
                asked = true;                               // tests: a planted difference compare mode must report
            }
            if (r == null && !asked) {
                int same = compareSame.incrementAndGet();
                if (same % 100 == 0) {
                    System.err.println("passscan: compare " + same + " skipped priorities agreed, " + compareDiffer.get() + " differed");
                }
            } else {
                compareDiffer.incrementAndGet();
                System.err.println("bridge: passscan compare DIFFERENCE turn " + turn + " " + phase + ": Forge "
                        + (asked ? "asked " + getPlayer().getName() : "returned " + r) + " where the skip would have passed");
            }
            return r;
        }

        static void compareSummary() {
            if ("compare".equals(skipScan)) {
                System.err.println("passscan: compare total " + compareSame.get() + " skipped priorities agreed, "
                        + compareDiffer.get() + " differed");
            }
        }
    }

    /** For tests: the controller class the bridge gives a human seat. */
    static Class<? extends PlayerController> controllerClass() {
        return Controller.class;
    }
}
