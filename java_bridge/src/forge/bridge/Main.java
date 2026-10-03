package forge.bridge;

import com.google.gson.JsonObject;

import forge.gui.GuiBase;
import forge.deck.Deck;
import forge.deck.io.DeckSerializer;
import forge.game.GameType;
import forge.game.GameView;
import forge.game.phase.PhaseType;
import forge.game.card.CardView;
import forge.game.player.PlayerView;
import forge.game.player.RegisteredPlayer;
import forge.gamemodes.match.HostedMatch;
import forge.gamemodes.match.YieldController;
import forge.gamemodes.match.YieldUpdate;
import forge.interfaces.IGameController;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.player.AutoYieldStore;
import forge.player.GamePlayerUtil;
import forge.player.LobbyPlayerHuman;
import forge.util.MyRandom;

import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Starts a Commander game between one human seat (driven over stdin/stdout by the Python client)
 * and any number of Forge AI opponents.
 *
 *   java -cp forge.jar:bridge.jar forge.bridge.Main --deck my.dck --opponent opp.dck [--opponent ...] [--seed N] [--name Karl]
 */
public class Main {
    static boolean dev = false;                          // --dev: tests and the card check may set up boards; a normal game never does
    /** Round MP1: two people share this engine (NetHost). Per-player settings then go on each player's own controller, never
     *  into Forge's global preferences, which both seats would read. */
    static boolean online = false;
    static HostedMatch match;
    /**
     * Round 28d: seconds an AI may think about one spell before Forge gives up on it (Forge's MATCH_AI_TIMEOUT, default 5).
     * At the timeout AiController asks its "Game AI Eval" thread to stop, but the thread only checks between spells, and the
     * last resort, Thread.stop(), throws on Java 20+ - so the thread keeps running beside the game thread, both rebuilding the
     * same cards' static abilities. Soak night 4 (game 3): the game thread died of that race (NoSuchElementException in
     * Card.updateStaticAbilities); night 6 (game 113): four timeouts in a row on a 114-card board and a 5-minute AI stall. A
     * longer limit means far fewer abandoned threads; the cost is a rare slow AI decision taking longer instead of being cut.
     */
    static final int AI_TIMEOUT_SECONDS = 20;
    static int aiTimeout = AI_TIMEOUT_SECONDS;
    /**
     * Round FMT1: the game's format. Commander (the default, and the only one online) or Brawl: MTG Arena's 100-card Brawl,
     * which Forge plays as its Brawl variant (RegisteredPlayer.forVariants: commanders from the deck, 25 life with two players
     * and 30 with more; GameRules.hasCommander() is true, so the command-zone rules run; Player.addCombatDamage skips commander
     * damage; MulliganService makes the first mulligan free). Forge doesn't check a deck against the format at the start of a
     * game (HostedMatch.startMatch), so a Brawl deck plays as it is listed, like a Commander deck.
     * Only a name here: touching GameType before initForge() (a static initialiser) loads its labels before Forge's
     * Localizer exists, and the bridge dies at start-up. variant() turns it into the GameType once Forge is up.
     */
    static String format = "commander";

    /** The game's variant: GameType.Brawl for --format brawl, else GameType.Commander (also for NetHost, which never sets it). */
    static GameType variant() {
        return "brawl".equals(format) ? GameType.Brawl : GameType.Commander;
    }

    public static void main(String[] args) throws Exception {
        PrintStream proto = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        System.setOut(System.err);                       // Forge chatters on System.out; keep the wire clean
        System.setProperty("java.awt.headless", "true");

        String deckPath = null, name = "You";
        List<String> opponents = new ArrayList<>();
        Long seed = null;
        Long parentPid = null;
        String badFormat = null;
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--deck": deckPath = args[++i]; break;
                case "--opponent": opponents.add(args[++i]); break;
                case "--seed": seed = Long.parseLong(args[++i]); break;
                case "--name": name = args[++i]; break;
                case "--dev": dev = true; break;
                case "--ai-timeout": aiTimeout = Integer.parseInt(args[++i]); break;
                case "--loop-cap": LoopGuard.cap = Integer.parseInt(args[++i]); break;      // round 28e, see LoopGuard (0 = off)
                case "--parent-pid": parentPid = Long.parseLong(args[++i]); break;        // round 29a, see ParentWatch
                case "--format": {                                                          // round FMT1: commander (default) or brawl
                    String f = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (f.equals("commander") || f.equals("brawl")) format = f;
                    else badFormat = args[i];
                    break;
                }
                case "--fault":                              // round 27d: a deliberate, known mistake for tests (honoured only with --dev)
                    try {
                        Checks.faults.add(Checks.Fault.valueOf(args[++i].toUpperCase()));
                    } catch (IllegalArgumentException e) {
                        System.err.println("bridge: unknown fault " + args[i]);
                    }
                    break;
                default: System.err.println("bridge: unknown argument " + args[i]);
            }
        }
        if (deckPath == null || opponents.isEmpty()) {
            System.err.println("usage: --deck FILE --opponent FILE [--opponent FILE ...] [--seed N] [--name NAME] [--format commander|brawl]");
            System.exit(2);
        }

        if (parentPid != null) {
            ParentWatch.start(parentPid);              // round 29a: before Forge starts (10-20 s), so a killed program never leaves java.exe behind
        }
        Wire wire = new Wire(proto);
        Checks.wire = wire;
        if (badFormat != null) {
            fail(wire, "unknown format " + badFormat + " (this bridge knows commander and brawl)");
            return;
        }
        if (!Checks.faults.isEmpty() && !dev) {
            System.err.println("bridge: --fault ignored (the bridge was not started with --dev)");
        }
        initForge(name, seed);


        Deck mine = DeckSerializer.fromFile(new File(deckPath));
        if (mine == null) {
            fail(wire, "could not read deck " + deckPath);
            return;
        }
        List<RegisteredPlayer> players = new ArrayList<>();
        int seats = 1 + opponents.size();
        RegisteredPlayer human = register(mine, seats);
        human.setPlayer(new LobbyPlayerHuman(name));
        players.add(human);
        int i = 1;
        for (String path : opponents) {
            Deck d = DeckSerializer.fromFile(new File(path));
            if (d == null) {
                fail(wire, "could not read deck " + path);
                return;
            }
            RegisteredPlayer rp = register(d, seats);
            rp.setPlayer(LoopGuard.guarded(GamePlayerUtil.createAiPlayer("AI " + i + " (" + d.getName() + ")", i, "")));   // round 28e
            players.add(rp);
            i++;
        }

        BridgeGui gui = new BridgeGui(wire);
        gui.events = new EventForwarder(wire);
        match = new HostedMatch();
        JsonObject ready = new JsonObject();
        ready.addProperty("t", "ready");
        ready.addProperty("protocol", 2);                    // 2 = "event" lines, snapshot fields eventSeq / input / inputSeq, "at" on clicks
        ready.addProperty("aiTimeout", aiTimeout);           // round 28d
        ready.addProperty("loopCap", LoopGuard.cap);         // round 28e
        ready.addProperty("format", format);                 // round FMT1: the Python side checks it
        ready.addProperty("startingLife", human.getStartingLife());
        wire.send(ready);
        // The applied-variant set must include Commander. With null (as before round 27c) Forge's GameRules had no applied variant,
        // so GameAction.stateBasedAction_Commander, which checks GameRules.hasAppliedVariant(Commander), never ran: nobody was asked
        // whether to move a commander from the graveyard or exile to the command zone. Found from Karl's bug report 2026-09-26
        // (Valgavoth exiled by Utter End, no question asked). Losing to 21 commander damage was NOT affected (checked live with the
        // old jar). Forge's own Commander lobby passes the same set.
        // Round FMT1: a Brawl game applies the Brawl variant instead (Forge's own Brawl lobby does the same).
        GameType variant = variant();
        java.util.Set<GameType> variants = Checks.fault(Checks.Fault.NO_COMMANDER_VARIANT)
                ? java.util.EnumSet.noneOf(GameType.class) : java.util.EnumSet.of(variant);
        match.startMatch(variant, variants, players, human, gui);

        wire.readLoop(cmd -> javax.swing.SwingUtilities.invokeLater(() -> handle(gui, cmd)));
        System.exit(0);
    }

    /** Round FMT1: one seat's RegisteredPlayer for the game's format. Commander is exactly what it was before (forCommander: 40
     *  life). Brawl goes through forVariants, which sets 25 life for two players and 30 for more (CR 903.12f). */
    static RegisteredPlayer register(Deck deck, int seats) {
        if (variant() == GameType.Brawl) {
            return RegisteredPlayer.forVariants(seats, java.util.EnumSet.of(GameType.Brawl), deck, null, false, null, null);
        }
        return RegisteredPlayer.forCommander(deck);
    }

    static void fail(Wire wire, String msg) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "fatal");
        m.addProperty("text", msg);
        wire.send(m);
        System.exit(1);
    }

    /** Everything Forge needs before a match can start, shared by Main and (round MP1) NetHost so the two never drift apart. */
    static void initForge(String name, Long seed) {
        HeapWatch.start();                             // round 28d: peak memory, sent with game_over
        GuiBase.setInterface(new HeadlessGui());       // round 28bb: GuiDesktop minus the skin-drawn trophy (winning froze the game)
        FModel.initialize(null, null);
        FModel.getPreferences().setPref(FPref.UI_ENABLE_SOUNDS, false);      // no sound card here; the events throw otherwise
        FModel.getPreferences().setPref(FPref.UI_ENABLE_MUSIC, false);
        FModel.getPreferences().setPref(FPref.UI_SHOW_ACTIONABLE_HIGHLIGHTS, true);   // "you could act with this" hints
        // Forge's own desktop window lets you pick cards straight out of its library / graveyard / exile displays. We have no such
        // floating displays, so turn that off: then Forge asks with a list of the cards instead (search, scry, tutor, recursion).
        FModel.getPreferences().setPref(FPref.UI_SELECT_FROM_CARD_DISPLAYS, false);
        FModel.getPreferences().setPref(FPref.UI_SHOW_AUTOTAP_PREVIEW, true);          // which sources auto-pay would tap
        FModel.getPreferences().setPref(FPref.PLAYER_NAME, name);             // skips the "what should we call you" prompt
        FModel.getPreferences().setPref(FPref.MATCH_AI_TIMEOUT, String.valueOf(aiTimeout));   // round 28d, see AI_TIMEOUT_SECONDS (memory only, never saved)
        if (seed != null) {
            MyRandom.setRandom(new java.util.Random(seed));
        }
    }

    /**
     * Round MP1 (found by tools/online_soak.py, 2 Oct): concede only while Forge's game thread is waiting for a person.
     * A concede takes the player's cards out of the game. Sent while the game thread was busy - working out which of a
     * player's cards can be played (AvailableActions, iterating every card in the game) - it changed that list under it:
     * ConcurrentModificationException, the game thread died and both tables waited for ever (2 of 14 concedes in the soak).
     * Every command runs on Swing's thread, so while one waits here no click can wake the game thread; the check and the
     * concede happen in one Swing task. Re-checked every 40 ms; after CONCEDE_WAIT_MS it concedes anyway (as before).
     */
    static final long CONCEDE_WAIT_MS = 10_000;

    static void concedeWhenSafe(BridgeGui gui, IGameController gc, long since) {
        forge.game.Game g = gc instanceof forge.game.player.PlayerController pc ? pc.getGame() : null;
        if (g != null && g.isGameOver()) {
            return;
        }
        boolean late = System.currentTimeMillis() - since > CONCEDE_WAIT_MS;
        boolean safe = g == null || (humanWaiting(g) && (gui == null || !gui.activatingManaAbility()));
        if (safe || late) {
            if (!safe) {
                System.err.println("gamesync: conceding although the game thread is busy (waited " + CONCEDE_WAIT_MS / 1000 + " s)");
            }
            gc.concede();
            return;
        }
        javax.swing.Timer t = new javax.swing.Timer(40, e -> concedeWhenSafe(gui, gc, since));
        t.setRepeats(false);
        t.start();
    }

    /** True while some person in the game has a question open: Forge's game thread is then parked waiting for the answer. */
    static boolean humanWaiting(forge.game.Game g) {
        for (forge.game.player.Player p : g.getRegisteredPlayers()) {
            if (p.getController() instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null
                    && h.getInputQueue().getInput() != null) {
                return true;
            }
        }
        return false;
    }

    /** Commands that answer "the question Forge is asking now"; with an "at" number they are dropped when it is out of date. */
    static final java.util.Set<String> STALE_CHECKED = java.util.Set.of("ok", "cancel", "card", "player", "mana");

    static void handle(BridgeGui gui, JsonObject cmd) {
        String c = cmd.get("c").getAsString();
        if (c.equals("quit")) {
            System.exit(0);
        }
        IGameController gc = gui.getGameController();
        if (gc == null) {
            System.err.println("bridge: no game controller yet for command " + c);
            return;
        }
        if (cmd.has("at") && STALE_CHECKED.contains(c) && !gui.clickIsCurrent(cmd.get("at").getAsLong())) {
            JsonObject d = new JsonObject();                   // a click made for a question Forge is no longer asking: drop it, visibly
            d.addProperty("t", "dropped");
            d.addProperty("c", c);
            d.addProperty("at", cmd.get("at").getAsLong());
            d.addProperty("now", gui.inputSeq());
            gui.wire.send(d);
            return;
        }
        // Round 28ba: a click on a greyed-out OK or Cancel is refused, as Forge's own window would never send one. Found by the
        // soak test: in a 3-4 player game, "Who would you like to start this game?" has OK greyed until a portrait is clicked,
        // but an OK sent anyway was accepted with nobody chosen, and Forge then crashed (NullPointerException on "takesAction")
        // and the game never started. Checked live before the fix (seed 5, three players).
        // Round 28bd: nothing that answers the current question while Forge is still activating a mana ability for it.
        // InputPayMana taps the source on a second thread (game.getAction().invoke, with its own "locked" flag); a Cancel
        // or OK in that moment restarts the game loop beside it, both threads walk Forge's static effects at once, and a
        // ConcurrentModificationException kills the game (soak night 3, games 132 and 151: tap a land that asks "which
        // ability?", answer, Cancel at once). Reproduced live with Shivan Reef + Treasure Cruise. Forge's own macro
        // system waits on the same flag (RecordActionsMacroSystem: isActivatingManaAbility). A person's click there is
        // dropped with reason "busy"; they click again a moment later.
        if (STALE_CHECKED.contains(c) && gui.activatingManaAbility()) {
            JsonObject d = new JsonObject();
            d.addProperty("t", "dropped");
            d.addProperty("c", c);
            d.addProperty("reason", "busy");
            d.addProperty("now", gui.inputSeq());
            gui.wire.send(d);
            return;
        }
        if ((c.equals("ok") && !gui.okEnabled) || (c.equals("cancel") && !gui.cancelEnabled)) {
            JsonObject d = new JsonObject();
            d.addProperty("t", "dropped");
            d.addProperty("c", c);
            d.addProperty("reason", "disabled");
            d.addProperty("now", gui.inputSeq());
            gui.wire.send(d);
            return;
        }
        switch (c) {
            case "ok": gc.selectButtonOk(); break;
            case "cancel": gc.selectButtonCancel(); break;
            case "undo": gc.undoLastAction(); break;
            case "alpha": gc.alphaStrike(); break;
            case "concede": concedeWhenSafe(gui, gc, System.currentTimeMillis()); break;
            case "card": {
                CardView cv = gui.cardIndex.get(cmd.get("id").getAsInt());
                if (cv != null) gc.selectCard(cv, null, null);
                else System.err.println("bridge: unknown card " + cmd.get("id"));
                break;
            }
            case "player": {
                PlayerView pv = gui.playerById(cmd.get("id").getAsInt());
                if (pv != null) gc.selectPlayer(pv, null);
                break;
            }
            case "mana": {                                     // spend one floating mana of this colour on the payment in progress
                byte atom = Snapshot.manaAtom(cmd.get("color").getAsString());
                if (atom != 0) gc.useMana(atom);
                else System.err.println("bridge: unknown mana colour " + cmd.get("color"));
                break;
            }
            case "flush": gui.markDirty(); break;
            case "setup": setupBoard(cmd); break;
            case "stops": {
                List<String> names = new ArrayList<>();
                for (com.google.gson.JsonElement e : cmd.getAsJsonArray("phases")) names.add(e.getAsString());
                gui.setStops(cmd.get("mine").getAsBoolean(), names);
                break;
            }
            case "yield": yieldCommand(gui, gc, cmd); break;
            case "autoyield": {                                // always pass (or stop always passing) this ability, by its stack key
                String key = cmd.get("key").getAsString();
                if (!key.isEmpty()) gc.setShouldAutoYield(key, cmd.get("on").getAsBoolean(), gc.getYieldController().isAbilityScope());
                gui.markDirty();
                break;
            }
            case "trigger": {                                  // always / never use this optional trigger (or ask again)
                String key = cmd.get("key").getAsString();
                AutoYieldStore.TriggerDecision d;
                switch (cmd.get("decision").getAsString()) {
                    case "accept": d = AutoYieldStore.TriggerDecision.ACCEPT; break;
                    case "decline": d = AutoYieldStore.TriggerDecision.DECLINE; break;
                    default: d = AutoYieldStore.TriggerDecision.ASK;
                }
                if (!key.isEmpty()) gc.setTriggerDecision(key, d, gc.getYieldController().isAbilityScope());
                gui.markDirty();
                break;
            }
            case "autopass": {                                 // pass by myself whenever Forge finds nothing I can do (an opponent's spell or attack still stops it)
                boolean on = cmd.get("on").getAsBoolean();
                if (online) {
                    // Round MP1: YieldController keeps a per-controller override that wins over FModel (Forge's own network
                    // host uses it for remote players); the global preference would switch auto-pass on for BOTH seats.
                    gc.getYieldController().setPref(FPref.YIELD_AUTO_PASS_NO_ACTIONS, String.valueOf(on));
                    gc.getYieldController().setPref(FPref.YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, "true");
                } else {
                    FModel.getPreferences().setPref(FPref.YIELD_AUTO_PASS_NO_ACTIONS, String.valueOf(on));
                    FModel.getPreferences().setPref(FPref.YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, "true");
                }
                gc.setYieldPref(FPref.YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, "true");
                gc.setYieldPref(FPref.YIELD_AUTO_PASS_NO_ACTIONS, String.valueOf(on));
                gui.markDirty();
                break;
            }
            case "yieldreset": {                               // forget every "always pass" and "always yes / no"
                gc.getYieldController().clearAutoYields();
                gc.getYieldController().clearActiveYieldAndDispatch();
                gui.markDirty();
                break;
            }
            default: System.err.println("bridge: unknown command " + c);
        }
    }

    /**
     * {"c":"setup","lines":["humanhand=Sol Ring","aibattlefield=Forest;Forest", ...]} - Forge's own "Setup Game State" (its developer menu, GameState.java):
     * replaces the named zones, sets life, phase and so on, at once. Only with --dev. Used by the card check and the tests; the game itself never sends it.
     */
    static void setupBoard(JsonObject cmd) {
        if (!dev) {
            System.err.println("bridge: setup ignored (the bridge was not started with --dev)");
            return;
        }
        try {
            List<String> lines = new ArrayList<>();
            for (com.google.gson.JsonElement e : cmd.getAsJsonArray("lines")) {
                lines.add(e.getAsString());
            }
            SetupState st = new SetupState();          // round 28c: says "setup_done" when finished (see SetupState)
            st.parse(lines);
            st.applyThenSignal(match.getGame(), Checks.wire);
        } catch (Exception e) {
            System.err.println("bridge: setup failed: " + e);
        }
    }

    /** {"c":"yield","mode":"turn"|"stack"|"stackall"|"until"|"clear", "phase":"UPKEEP"} - the same calls Forge's own window makes. */
    static void yieldCommand(BridgeGui gui, IGameController gc, JsonObject cmd) {
        PlayerView local = gui.me();
        if (local == null) return;
        switch (cmd.get("mode").getAsString()) {
            case "turn": gc.sendYieldUpdate(new YieldUpdate.SetAutoPassUntilEndOfTurn(local, true)); break;
            case "stack": gc.sendYieldUpdate(new YieldUpdate.StackYield(local, true, true)); break;        // until the stack is empty; an opponent's new spell stops it
            case "stackall": gc.sendYieldUpdate(new YieldUpdate.StackYield(local, true, false)); break;    // until the stack is empty, whatever happens
            case "until": {                                    // until this phase of my next turn
                PhaseType ph = PhaseType.valueOf(cmd.get("phase").getAsString());
                GameView gv = gui.getGameView();
                boolean atOrPast = gv != null && YieldController.isPriorityAtOrPastMarker(gv, local, ph);
                gc.sendYieldUpdate(new YieldUpdate.SetMarker(local, ph, atOrPast));
                break;
            }
            case "clear": gc.getYieldController().clearActiveYieldAndDispatch(); break;
            default: System.err.println("bridge: unknown yield mode " + cmd.get("mode"));
        }
        gui.markDirty();
    }
}
