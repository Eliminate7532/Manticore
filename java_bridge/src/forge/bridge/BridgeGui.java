package forge.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import forge.card.MagicColor;
import forge.deck.CardPool;
import forge.game.GameEntityView;
import forge.game.GameLogEntry;
import forge.game.GameState;
import forge.game.GameView;
import forge.game.card.CardView;
import forge.game.card.CounterEnumType;
import forge.game.player.DelayedReveal;
import forge.game.player.PlayerView;
import forge.game.spellability.SpellAbilityView;
import forge.game.phase.PhaseType;
import forge.gamemodes.match.AbstractGuiGame;
import forge.item.PaperCard;
import forge.localinstance.skin.FSkinProp;
import forge.player.PlayerZoneUpdate;
import forge.util.FSerializableFunction;
import forge.util.ITriggerEvent;
import forge.game.player.IHasIcon;
import forge.LobbyPlayer;
import forge.trackable.TrackableCollection;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Forge's IGuiGame, implemented as a remote control: nothing is drawn here. Every change marks the
 * state dirty and a JSON snapshot goes to the Python client; every question the engine asks the human
 * becomes a "request" the client must answer.
 */
public class BridgeGui extends AbstractGuiGame {
    final Wire wire;
    final Map<Integer, CardView> cardIndex = new java.util.concurrent.ConcurrentHashMap<>();
    volatile String prompt = "";
    volatile CardView promptCard = null;
    volatile String okLabel = "OK", cancelLabel = "Cancel";
    volatile boolean okEnabled = false, cancelEnabled = false, okFocus = false;
    private volatile PlayerView me;
    private final AtomicBoolean dirty = new AtomicBoolean(false);
    private final ScheduledExecutorService flusher = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "bridge-flusher");
        t.setDaemon(true);
        return t;
    });
    private int logSent = 0;

    /** Phases where the human wants priority even with an empty stack (everything else is passed automatically). */
    final java.util.Set<PhaseType> stopsMine = java.util.concurrent.ConcurrentHashMap.newKeySet();
    final java.util.Set<PhaseType> stopsTheirs = java.util.concurrent.ConcurrentHashMap.newKeySet();

    {
        java.util.Collections.addAll(stopsMine, PhaseType.MAIN1, PhaseType.COMBAT_DECLARE_ATTACKERS, PhaseType.MAIN2,
                PhaseType.END_OF_TURN);
        java.util.Collections.addAll(stopsTheirs, PhaseType.UPKEEP, PhaseType.COMBAT_DECLARE_ATTACKERS, PhaseType.END_OF_TURN);
    }

    void setStops(boolean mine, java.util.Collection<String> names) {
        java.util.Set<PhaseType> target = mine ? stopsMine : stopsTheirs;
        target.clear();
        for (String n : names) {
            try {
                target.add(PhaseType.valueOf(n));
            } catch (IllegalArgumentException e) {
                System.err.println("bridge: unknown phase " + n);
            }
        }
        markDirty();
    }

    public BridgeGui(Wire wire) {
        this.wire = wire;
    }

    /** Round MP2: an online spectator's view. It has no player, so Forge never asks it anything; its snapshots show every
     *  hidden card (hands, libraries, face-down cards) face down (Snapshot.build). */
    volatile boolean spectator = false;

    /** Round MP2: Forge calls this from HostedMatch.registerSpectator. A spectator joins after the game started, so this is
     *  where it joins the game's event bus (a player does that in setOriginalGameController). */
    @Override
    public void setSpectator(forge.interfaces.IGameController controller) {
        super.setSpectator(controller);
        if (events != null && controller instanceof forge.game.player.PlayerController pc) {
            forge.game.Game g = pc.getGame();
            if (g != null && g != subscribedTo) {
                subscribedTo = g;
                g.subscribeToEvents(events);
            }
        }
        markDirty();
    }

    // ---- game events (sounds / animations on the table) ------------------------------------
    EventForwarder events;                          // set by Main; null = no event lines (the table then falls back to comparing snapshots)
    private forge.game.Game subscribedTo;

    /** Forge calls this for the human seat in HostedMatch.startGame, BEFORE the game thread starts: the one moment to join the
     *  game's event bus without missing the opening shuffle and mulligans. */
    @Override
    public void setOriginalGameController(PlayerView player, forge.interfaces.IGameController gameController) {
        super.setOriginalGameController(player, gameController);
        if (gameController instanceof forge.game.player.PlayerController pcc && pcc.getGame() != null
                && !pcc.getGame().getRules().hasAppliedVariant(Main.variant())) {
            // Round 27d: without the Commander variant, Forge silently skips Commander-only rules (round 27c's missing
            // "move your commander to the command zone?" question). Round FMT1: a Brawl game needs the Brawl variant instead.
            Checks.fail("start", "commander_variant_missing", "the game's rules do not have the " + Main.variant() + " variant applied");
        }
        if (events != null && gameController instanceof forge.game.player.PlayerController pc) {
            forge.game.Game g = pc.getGame();
            if (g != null && g != subscribedTo) {
                subscribedTo = g;
                g.subscribeToEvents(events);
            }
        }
        if (gameController instanceof forge.player.PlayerControllerHuman h && !h.hasCheated()) {
            // Round 28bb: no achievements. Forge skips them for a player who "has cheated" (AchievementCollection.updateAll),
            // and cheat() only creates the dev-mode helper - it does nothing by itself. Achievements mean nothing in this
            // program, and updating them at game over crashed on the trophy picture, so winning a game froze it (the soak
            // test's game 103). HeadlessGui is the second guard.
            h.cheat();
        }
        if (gameController instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null && watchedQueue != h.getInputQueue()) {
            watchedQueue = h.getInputQueue();
            watchedQueue.addObserver((queue, arg) -> {
                noteInput(watchedQueue.getInput());
                releaseIfGameOver(h);
            });
            noteInput(watchedQueue.getInput());
        }
    }

    // ---- question numbers: which of Forge's questions a click answers --------------------------------------------------
    // Forge applies an OK or a card click to whatever it is asking at the moment the click arrives, and ignores it when it is
    // asking nothing. So a click made while the screen was out of date could land on the NEXT question, or vanish, depending on
    // timing, and a replayed game (Resume last game, bug-report replay) could not know which. Every change of the human's input
    // (including "no question now") gets the next number, counted from Forge's own InputQueue notifications (not by sampling),
    // so the numbering is the same in every run of the same game. Snapshots carry the current number as "inputSeq"; clicks carry
    // the number they answer as "at"; Main.handle drops a click whose number is no longer current and says so ("dropped").
    private forge.gamemodes.match.input.InputQueue watchedQueue;
    private Object lastInput;
    private long inputSeq = 0;

    private synchronized void noteInput(Object input) {
        if (input != lastInput) {
            lastInput = input;
            inputSeq++;
        }
    }

    /**
     * Round MP1 (found by tools/online_soak.py, 2 Oct): a question Forge asks AFTER the game has ended is released at once.
     * A concede comes from another thread than the game's. PlayerControllerHuman.concede() ends the game and releases the
     * questions waiting at that moment (InputQueue.onGameOver), but when the game thread was between two questions it goes on
     * to ask the next one (a priority pass) after that - and nobody ever answers it: the game thread waits for ever, Forge
     * never finishes the game and "game_over" is never sent. Seen once in 7 concedes in the online soak (the guest conceded in
     * its own upkeep; both tables then said "Waiting for ..." for good). Forge's own code releases a late question the same way
     * (relaseLatchWhenGameIsOver). The log line deliberately doesn't start with "bridge:" (card_check.PROBLEM_LINE).
     */
    private void releaseIfGameOver(forge.player.PlayerControllerHuman h) {
        try {
            forge.gamemodes.match.input.InputQueue q = h.getInputQueue();
            if (q == null || q.getInput() == null) {
                return;
            }
            forge.game.Game g = h.getGame();
            if (g != null && g.isGameOver()) {
                System.err.println("gamesync: released a question asked after the game ended ("
                        + q.getInput().getClass().getSimpleName() + ")");
                q.onGameOver(true);
            }
        } catch (RuntimeException e) {
            System.err.println("gamesync: could not release a late question: " + e);
        }
    }

    /** The current question number (see above). */
    synchronized long inputSeq() {
        if (watchedQueue != null) {
            noteInput(watchedQueue.getInput());        // an update we have not been notified of yet (never lowers the number)
        }
        return inputSeq;
    }

    /** True when a click made at question `at` may be applied now: that question is still the current one and it is a real question. */
    synchronized boolean clickIsCurrent(long at) {
        return inputSeq() == at && lastInput != null;
    }

    /** The class name of what Forge is waiting for from the human right now ("InputPassPriority", "InputPayMana...", ...), or "". */
    String inputKind() {
        try {
            forge.interfaces.IGameController gc = getGameController();
            if (gc instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null) {
                Object in = h.getInputQueue().getInput();
                return in == null ? "" : in.getClass().getSimpleName();
            }
        } catch (RuntimeException e) {
            // fall through: unknown
        }
        return "";
    }

    /**
     * Round 28bb: true while Forge is waiting for the human to answer something (its InputQueue has an input), false between
     * two questions and while an AI thinks. "input" can't tell: it is "" both then AND for an anonymous Input class (the
     * cleanup discard is one), and the snapshot keeps showing the last question's text and buttons in between. The soak bot
     * answered those in-between snapshots and the bridge dropped the clicks (about 100 a game).
     */
    boolean asking() {
        try {
            forge.interfaces.IGameController gc = getGameController();
            if (gc instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null) {
                return h.getInputQueue().getInput() != null;
            }
        } catch (RuntimeException e) {
            // fall through: unknown, say yes so nobody waits forever on it
        }
        return true;
    }

    /**
     * Round 28d: the players the current question lets me pick, and those already picked, by player id. Forge's own desktop
     * never marks selectable PLAYERS (setSelectables takes cards only; you just click a portrait), so a "choose a player" list
     * question - a Siege's "Choose an opponent to protect this battle" - showed nothing clickable, OK stayed greyed, and the
     * soak bot never answered it (nights 5 and 6, Invasion of Ikoria). InputSelectEntitiesFromList exposes its valid choices
     * and its current picks, so both are read from there. Targeting (InputSelectTargets) keeps its choices private: not covered.
     * Returns null when the current question isn't such a list.
     */
    java.util.Map<Integer, Boolean> playerChoices() {
        try {
            forge.interfaces.IGameController gc = getGameController();
            if (gc instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null
                    && h.getInputQueue().getInput() instanceof forge.gamemodes.match.input.InputSelectEntitiesFromList<?> in) {
                java.util.Map<Integer, Boolean> out = new java.util.HashMap<>();
                for (Object o : in.getValidChoices()) {
                    if (o instanceof forge.game.player.Player p) out.put(p.getId(), false);
                }
                for (Object o : in.getSelected()) {
                    if (o instanceof forge.game.player.Player p && out.containsKey(p.getId())) out.put(p.getId(), true);
                }
                return out.isEmpty() ? null : out;
            }
        } catch (RuntimeException e) {
            // the game thread changed the list under us: leave the players unmarked for this one snapshot
        }
        return null;
    }

    /** Round 28bd: Forge is tapping a mana source for the current payment on its own thread (see Main.handle). */
    boolean activatingManaAbility() {
        try {
            forge.interfaces.IGameController gc = getGameController();
            if (gc instanceof forge.player.PlayerControllerHuman h && h.getInputQueue() != null
                    && h.getInputQueue().getInput() instanceof forge.gamemodes.match.input.InputPayMana pay) {
                return pay.isActivatingManaAbility();
            }
        } catch (RuntimeException e) {
            // fall through
        }
        return false;
    }

    String promptMessage() {
        return prompt;
    }

    CardView promptCard() {
        return promptCard;
    }

    PlayerView me() {
        if (me != null) {
            return me;
        }
        for (PlayerView p : getLocalPlayers()) {
            return p;
        }
        return null;
    }

    PlayerView playerById(int id) {
        GameView gv = getGameView();
        if (gv == null) return null;
        for (PlayerView p : gv.getPlayers()) {
            if (p.getId() == id) return p;
        }
        return null;
    }

    // ---- pushing state --------------------------------------------------------------------

    void markDirty() {
        if (wire.isClosed()) {
            return;                                 // round MP2: a spectator who left (or a seat given up): nobody to send to
        }
        if (dirty.compareAndSet(false, true)) {
            flusher.schedule(this::flush, 40, TimeUnit.MILLISECONDS);
        }
    }

    /** Round MP2b: snapshots sent so far (a played-back online game waits until a seat's view stops changing). */
    volatile long flushes = 0;

    void flush() {
        dirty.set(false);
        flushes++;
        GameView gv = getGameView();
        if (gv == null) {
            return;
        }
        for (int attempt = 0; attempt < 5; attempt++) {
            try {
                long eventSeq = events == null ? -1 : events.lastSeq();      // read FIRST: the snapshot then shows at least these events' results
                JsonObject snap = Snapshot.build(gv, me(), this, cardIndex);
                if (eventSeq >= 0) snap.addProperty("eventSeq", eventSeq);
                snap.addProperty("input", inputKind());
                snap.addProperty("asking", asking());
                snap.addProperty("inputSeq", inputSeq());
                wire.send(snap);
                sendNewLogLines(gv);
                return;
            } catch (java.util.ConcurrentModificationException e) {
                // the game thread changed a collection while we read it: try again
                try {
                    Thread.sleep(15);
                } catch (InterruptedException ie) {
                    return;
                }
            } catch (Throwable t) {
                System.err.println("bridge: snapshot failed");
                t.printStackTrace();
                return;
            }
        }
        markDirty();
    }

    private void sendNewLogLines(GameView gv) {
        if (gv.getGameLog() == null) return;
        List<GameLogEntry> all = gv.getGameLog().getAllEntries();
        if (all.size() <= logSent) return;
        JsonArray arr = new JsonArray();
        for (int i = logSent; i < all.size(); i++) {
            GameLogEntry e = all.get(i);
            JsonObject l = new JsonObject();
            l.addProperty("type", e.type().name());
            l.addProperty("text", e.message());
            arr.add(l);
        }
        logSent = all.size();
        JsonObject msg = new JsonObject();
        msg.addProperty("t", "log");
        msg.add("entries", arr);
        wire.send(msg);
    }

    // ---- small helpers for requests -------------------------------------------------------

    static String strip(String s) {
        return s == null ? "" : s.replaceAll("<[^>]*>", "");
    }

    JsonObject entity(GameEntityView e) {
        JsonObject o = new JsonObject();
        if (e instanceof CardView) {
            CardView c = (CardView) e;
            cardIndex.put(c.getId(), c);
            o.addProperty("kind", "card");
            o.add("card", Snapshot.card(c, me(), null));
        } else if (e instanceof PlayerView) {
            o.addProperty("kind", "player");
            o.addProperty("id", e.getId());
            o.addProperty("name", ((PlayerView) e).getName());
        } else {
            o.addProperty("kind", "other");
            o.addProperty("name", String.valueOf(e));
        }
        return o;
    }

    <T> JsonObject item(T t, FSerializableFunction<T, String> display) {
        if (t instanceof GameEntityView) {
            return entity((GameEntityView) t);
        }
        JsonObject o = new JsonObject();
        o.addProperty("kind", "text");
        String label;
        if (display != null) {
            label = display.apply(t);
        } else if (t instanceof SpellAbilityView) {
            label = ((SpellAbilityView) t).getDescription();
        } else {
            label = String.valueOf(t);
        }
        o.addProperty("label", strip(label));
        return o;
    }

    /** Send a question and turn the answer (array of indexes) into the chosen items. */
    <T> List<T> ask(String kind, String title, int min, int max, List<T> choices, FSerializableFunction<T, String> display,
                    CardView source) {
        JsonObject req = new JsonObject();
        req.addProperty("kind", kind);
        req.addProperty("title", strip(title));
        req.addProperty("min", min);
        req.addProperty("max", max);
        if (source != null) {
            cardIndex.put(source.getId(), source);
            req.add("source", Snapshot.card(source, me(), null));
        }
        JsonArray items = new JsonArray();
        for (T t : choices) {
            items.add(item(t, display));
        }
        req.add("items", items);
        JsonElement reply = wire.request(req);
        List<T> out = new ArrayList<>();
        List<JsonElement> raw = new ArrayList<>();
        if (reply != null && reply.isJsonArray()) {
            reply.getAsJsonArray().forEach(raw::add);
        } else if (reply != null && reply.isJsonPrimitive()) {
            raw.add(reply);
        }
        java.util.Set<Integer> used = new java.util.HashSet<>();
        for (JsonElement e : raw) {
            int i;
            try {
                i = e.getAsInt();
            } catch (RuntimeException ex) {
                Checks.unusable(kind, strip(title) + ": answer " + e + " is not an item number");
                continue;
            }
            if (i < 0 || i >= choices.size()) {
                Checks.unusable(kind, strip(title) + ": item " + i + " of " + choices.size() + " does not exist");
            } else if (!used.add(i)) {
                Checks.unusable(kind, strip(title) + ": item " + i + " answered twice");
            } else {
                out.add(choices.get(i));
            }
        }
        return out;
    }

    // ---- prompt and buttons ---------------------------------------------------------------

    @Override
    public void showPromptMessage(PlayerView playerView, String message, CardView card) {
        prompt = strip(message);
        // Forge passes the single card this prompt is about here for effects like surveil/scry
        // ("look at top card, choose top of library or graveyard"): remember it so the snapshot
        // can send it along and the client can show a thumbnail next to the text, the same way
        // confirm()/ask() already attach a "source" card. Most prompts pass null; that clears it.
        if (card != null) {
            cardIndex.put(card.getId(), card);
        }
        promptCard = card;
        markDirty();
    }

    @Override
    public void updateButtons(PlayerView owner, String label1, String label2, boolean enable1, boolean enable2, boolean focus1) {
        okLabel = strip(label1);
        cancelLabel = strip(label2);
        okEnabled = enable1;
        cancelEnabled = enable2;
        okFocus = focus1;
        markDirty();
    }

    // Round 28bb: "can I pick this card?" is kept by card id. Forge keeps it as a set of CardView objects compared by identity,
    // and some choices are offered as views of a copy of the card (Forge's "last known information"), not the view the
    // snapshot sends - so the right cards were never marked. Found by the soak test (Pariah, Retether: "select a creature to
    // attach to" with nothing highlighted, though clicking a creature worked). Reproduced live with Retether before the fix.
    // The id set is also safe to read while Forge's game thread changes it (the snapshot is built on another thread).
    private final java.util.Set<Integer> selectableIds = java.util.concurrent.ConcurrentHashMap.newKeySet();

    @Override
    public void setSelectables(Iterable<CardView> cards, int min, int max) {
        super.setSelectables(cards, min, max);
        for (CardView c : cards) {
            if (c != null) selectableIds.add(c.getId());
        }
        markDirty();
    }

    @Override
    public void clearSelectables() {
        super.clearSelectables();
        selectableIds.clear();
        markDirty();
    }

    @Override
    public boolean isSelectable(CardView card) {
        return card != null && selectableIds.contains(card.getId());
    }

    @Override
    public void setHighlighted(Iterable<GameEntityView> entities, boolean b) {
        super.setHighlighted(entities, b);
        markDirty();
    }

    // ---- change notifications: all just "send a new snapshot" -----------------------------

    @Override protected void updateCurrentPlayer(PlayerView player) { me = player; markDirty(); }
    @Override public void updateCards(Iterable<CardView> cards) { markDirty(); }
    @Override public void updateZones(Iterable<PlayerZoneUpdate> zonesToUpdate) { markDirty(); }
    @Override public void updatePhase(boolean saveState) { markDirty(); }
    @Override public void updateTurn(PlayerView player) { markDirty(); }
    @Override public void updateLives(Iterable<PlayerView> livesUpdate) { markDirty(); }
    @Override public void updateManaPool(Iterable<PlayerView> manaPoolUpdate) { markDirty(); }
    @Override public void updateShards(Iterable<PlayerView> shardsUpdate) { markDirty(); }
    @Override public void updateStack() { markDirty(); }
    @Override public void updatePlayerControl() { markDirty(); }
    @Override public void showCombat() { markDirty(); }
    @Override public void showManaPool(PlayerView player) { markDirty(); }
    @Override public void hideManaPool(PlayerView player) { markDirty(); }
    @Override public void refreshField() { markDirty(); }
    @Override public void openView(TrackableCollection<PlayerView> myPlayers) { markDirty(); }
    @Override public void alertUser() { }
    @Override public void flashIncorrectAction() {
        JsonObject m = new JsonObject();
        m.addProperty("t", "bad_action");
        wire.send(m);
    }
    @Override public void enableOverlay() { }
    @Override public void disableOverlay() { }
    @Override public void setCard(CardView card) { }
    @Override public void setPanelSelection(CardView hostCard) { }
    @Override public void setPlayerAvatar(LobbyPlayer player, IHasIcon ihi) { }
    @Override
    public boolean isUiSetToSkipPhase(PlayerView playerTurn, PhaseType phase) {
        PlayerView m = me();
        boolean mine = m != null && m.equals(playerTurn);
        return !(mine ? stopsMine : stopsTheirs).contains(phase);
    }
    @Override public GameState getGamestate() { return null; }

    @Override
    public void finishGame() {
        flush();
        JsonObject m = new JsonObject();
        m.addProperty("t", "game_over");
        m.addProperty("peakHeapMb", HeapWatch.peakMb());          // round 28d: memory the game needed (see HeapWatch)
        m.addProperty("peakLiveMb", HeapWatch.peakLiveMb());
        m.addProperty("maxHeapMb", HeapWatch.maxMb());
        wire.send(m);
    }

    // ---- questions the engine asks the human ----------------------------------------------

    @Override
    public boolean confirm(CardView c, String question, boolean defaultIsYes, List<String> options) {
        JsonObject req = new JsonObject();
        req.addProperty("kind", "confirm");
        req.addProperty("title", strip(question));
        req.addProperty("default", defaultIsYes);
        JsonArray opts = new JsonArray();
        if (options != null) for (String s : options) opts.add(strip(s));
        req.add("options", opts);
        if (c != null) {
            cardIndex.put(c.getId(), c);
            req.add("source", Snapshot.card(c, me(), null));
        }
        JsonElement r = wire.request(req);
        Checks.shape("confirm", c != null ? "card" : "no_card");
        if (r != null && r.isJsonPrimitive() && r.getAsJsonPrimitive().isBoolean()) return r.getAsBoolean();
        Checks.unusable("confirm", strip(question) + ": answer " + r + " is not yes/no; used the default");
        return defaultIsYes;
    }

    @Override
    public boolean showConfirmDialog(String message, String title, String yesButtonText, String noButtonText, boolean defaultYes) {
        JsonObject req = new JsonObject();
        req.addProperty("kind", "confirm");
        req.addProperty("title", strip(message));
        req.addProperty("default", defaultYes);
        JsonArray opts = new JsonArray();
        opts.add(strip(yesButtonText));
        opts.add(strip(noButtonText));
        req.add("options", opts);
        JsonElement r = wire.request(req);
        Checks.shape("confirm", "dialog");
        if (r != null && r.isJsonPrimitive() && r.getAsJsonPrimitive().isBoolean()) return r.getAsBoolean();
        Checks.unusable("confirm", strip(message) + ": answer " + r + " is not yes/no; used the default");
        return defaultYes;
    }

    @Override
    public int showOptionDialog(String message, String title, FSkinProp icon, List<String> options, int defaultOption) {
        List<String> picked = ask("choose", message, 1, 1, options, null, null);
        if (picked.isEmpty()) return defaultOption;
        return options.indexOf(picked.get(0));
    }

    @Override
    public String showInputDialog(String message, String title, FSkinProp icon, String initialInput, List<String> inputOptions, boolean isNumeric) {
        JsonObject req = new JsonObject();
        req.addProperty("kind", "input");
        req.addProperty("title", strip(message));
        req.addProperty("initial", initialInput == null ? "" : initialInput);
        req.addProperty("numeric", isNumeric);
        JsonArray opts = new JsonArray();
        if (inputOptions != null) for (String s : inputOptions) opts.add(s);
        req.add("options", opts);
        JsonElement r = wire.request(req);
        return r != null && r.isJsonPrimitive() ? r.getAsString() : initialInput;
    }

    @Override
    public <T> List<T> getChoices(String message, int min, int max, List<T> choices, List<T> selected,
                                  FSerializableFunction<T, String> display) {
        if (choices == null || choices.isEmpty()) {
            return new ArrayList<>();
        }
        if (min < 0 || max < 0) {                    // Forge uses (-1, -1) for "here is a list, just show it"
            JsonObject info = new JsonObject();
            info.addProperty("t", "info");
            info.addProperty("title", strip(message));
            JsonArray items = new JsonArray();
            for (T t : choices) {
                items.add(item(t, display));
            }
            info.add("items", items);
            wire.send(info);
            return new ArrayList<>();
        }
        List<T> got = ask("choose", message, min, max, choices, display, null);
        if (got.size() < min) {                      // an unusable answer: fall back to the first legal choices
            Checks.unusable("choose", strip(message) + ": " + got.size() + " chosen, at least " + min + " needed; used the first");
            got = new ArrayList<>(choices.subList(0, Math.min(min, choices.size())));
        }
        if (Checks.fault(Checks.Fault.CHOICES_OVER_MAX) && got.size() < choices.size()) {
            for (T t : choices) if (!got.contains(t)) { got.add(t); break; }
        }
        Checks.choices(strip(message), min, max, choices, got);
        return got;
    }

    @Override
    public GameEntityView chooseSingleEntityForEffect(String title, List<? extends GameEntityView> optionList,
                                                      DelayedReveal delayedReveal, boolean isOptional) {
        if (optionList == null || optionList.isEmpty()) return null;
        List<GameEntityView> opts = new ArrayList<>(optionList);
        List<GameEntityView> got = ask(isOptional ? "choose_optional" : "choose", title, isOptional ? 0 : 1, 1, opts, null, null);
        GameEntityView answer;
        if (got.isEmpty()) {
            if (!isOptional) Checks.unusable("choose_one", strip(title) + ": nothing chosen for a required choice; used the first");
            answer = isOptional ? null : opts.get(0);
        } else {
            answer = got.get(0);
        }
        Checks.single(strip(title), opts, answer, isOptional);
        return answer;
    }

    @Override
    public List<GameEntityView> chooseEntitiesForEffect(String title, List<? extends GameEntityView> optionList, int min, int max,
                                                        DelayedReveal delayedReveal) {
        List<GameEntityView> opts = new ArrayList<>(optionList);
        if (opts.isEmpty()) return opts;
        List<GameEntityView> got = ask("choose", title, min, max, opts, null, null);
        if (got.size() < min) {
            Checks.unusable("choose", strip(title) + ": " + got.size() + " chosen, at least " + min + " needed; used the first");
            got = new ArrayList<>(opts.subList(0, Math.min(min, opts.size())));
        }
        Checks.choices(strip(title), min, max, opts, got);
        return got;
    }

    @Override
    public SpellAbilityView getAbilityToPlay(CardView hostCard, List<SpellAbilityView> abilities, ITriggerEvent triggerEvent) {
        if (abilities == null || abilities.isEmpty()) return null;
        if (abilities.size() == 1) return abilities.get(0);
        List<SpellAbilityView> got = ask("choose_optional", "Choose an ability", 0, 1, abilities, null, hostCard);
        SpellAbilityView answer = got.isEmpty() ? null : got.get(0);
        Checks.single("Choose an ability", abilities, answer, true);
        return answer;
    }

    @Override
    public <T> OrderResult<T> order(String title, String top, int remainingObjectsMin, int remainingObjectsMax, List<T> sourceChoices,
                                    List<T> destChoices, CardView referenceCard, boolean sideboardingMode, boolean showRememberCheckbox) {
        OrderResult<T> r = orderAnswer(title, remainingObjectsMin, remainingObjectsMax, sourceChoices, destChoices, referenceCard);
        Checks.order(strip(title), remainingObjectsMin, remainingObjectsMax, sourceChoices, destChoices, r == null ? null : r.ordered());
        return r;
    }

    private <T> OrderResult<T> orderAnswer(String title, int remainingObjectsMin, int remainingObjectsMax, List<T> sourceChoices,
                                           List<T> destChoices, CardView referenceCard) {
        // Ordering (triggers, blockers, library cards) and "choose some of these, in order" (opening-hand effects).
        // remainingObjectsMin/Max say how many may stay unchosen: (0, 0) = order all of them; -1 = no limit on that side.
        // Round 27c: Forge also passes items that are ALREADY in order, in destChoices. The second time the same simultaneous
        // triggers come up (Spiteful Visions on every draw step), PlayerControllerHuman.orderSimultaneousSa offers the previous
        // order to re-confirm: every item is in destChoices and sourceChoices is empty. Reading only sourceChoices returned an
        // empty order, and Forge then put NONE of the triggers on the stack (Karl's bug report 2026-09-26: Spiteful Visions
        // never triggered on his own draw steps). The already-ordered items come first, in their order, then the rest.
        List<T> ordered = new ArrayList<>();
        if (destChoices != null && !Checks.fault(Checks.Fault.ORDER_IGNORES_SORTED)) ordered.addAll(destChoices);
        if (sourceChoices != null) ordered.addAll(sourceChoices);
        int n = ordered.size();
        if (n == 0) {
            return new OrderResult<>(ordered, false);
        }
        int minPick = remainingObjectsMax < 0 ? 0 : Math.max(0, n - remainingObjectsMax);
        int maxPick = remainingObjectsMin < 0 ? n : Math.max(0, n - remainingObjectsMin);
        boolean chooseSome = minPick != maxPick || maxPick != n;
        if (!chooseSome && n < 2) {
            return new OrderResult<>(ordered, false);
        }
        List<T> got = ask("order", title, minPick, maxPick, ordered, null, referenceCard);
        if (got.size() >= minPick && got.size() <= maxPick) {
            return new OrderResult<>(got, false);
        }
        Checks.unusable("order", strip(title) + ": " + got.size() + " items answered, " + minPick + ".." + maxPick + " allowed; used "
                + (chooseSome ? "none" : "Forge's order"));
        return new OrderResult<>(chooseSome ? new ArrayList<T>() : ordered, false);
    }

    @Override
    public List<CardView> manipulateCardList(String title, Iterable<CardView> cards, Iterable<CardView> manipulable,
                                             boolean toTop, boolean toBottom, boolean toAnywhere) {
        Checks.autoAnswered("manipulate", strip(title) + ": cards left in their current order (the table has no dialog for this)");
        List<CardView> list = new ArrayList<>();
        for (CardView c : cards) list.add(c);
        return list;
    }

    /** Lethal damage this attacker must give a blocker (or planeswalker) before the next one may get any. */
    private static int lethalFor(CardView c, boolean deathtouch) {
        int lethal = Math.max(0, c.getLethalDamage());
        if (c.getCurrentState().isPlaneswalker()) {
            try {
                lethal = Integer.parseInt(c.getCurrentState().getLoyalty());
            } catch (NumberFormatException e) {
                // keep the damage-based figure
            }
        } else if (deathtouch) {
            lethal = Math.min(lethal, 1);
        }
        return lethal;
    }

    private int defenderLethal(GameEntityView defender, boolean infect) {
        try {
            if (defender instanceof PlayerView) {
                PlayerView p = (PlayerView) defender;
                GameView gv = getGameView();
                return infect && gv != null ? gv.getPoisonCountersToLose() - p.getCounters(CounterEnumType.POISON) : p.getLife();
            }
            if (defender instanceof CardView) {
                return Integer.parseInt(((CardView) defender).getCurrentState().getLoyalty());
            }
        } catch (RuntimeException e) {
            // fall through: unknown
        }
        return 0;
    }

    @Override
    public Map<CardView, Integer> assignCombatDamage(CardView attacker, List<CardView> blockers, int damage, GameEntityView defender,
                                                     boolean overrideOrder, boolean maySkip) {
        // Rows: the blockers in damage order, then the defender when the attacker may hit past them (trample) or divide freely.
        boolean deathtouch = attacker.getCurrentState().hasDeathtouch();
        boolean infect = attacker.getCurrentState().hasInfect();
        boolean trample = defender != null && attacker.getCurrentState().hasTrample();
        boolean divide = attacker.getCurrentState().hasDivideDamage();
        boolean defenderRow = trample || (defender != null && divide && overrideOrder);
        List<CardView> bl = blockers == null ? new ArrayList<CardView>() : blockers;
        int n = bl.size() + (defenderRow ? 1 : 0);
        Map<CardView, Integer> result = new HashMap<>();
        if (n == 0) {
            result.put(null, damage);
            return result;
        }
        int[] lethal = new int[n];
        JsonArray rows = new JsonArray();
        for (int i = 0; i < bl.size(); i++) {
            CardView b = bl.get(i);
            lethal[i] = lethalFor(b, deathtouch);
            JsonObject row = entity(b);
            row.addProperty("lethal", lethal[i]);
            rows.add(row);
        }
        if (defenderRow) {
            lethal[n - 1] = defenderLethal(defender, infect);
            JsonObject row = entity(defender);
            row.addProperty("lethal", lethal[n - 1]);
            row.addProperty("defender", true);
            rows.add(row);
        }
        boolean free = divide && overrideOrder;
        boolean order = !overrideOrder;
        int[] amounts = Assign.autoDamage(damage, lethal);
        JsonObject req = new JsonObject();
        req.addProperty("kind", "assign");
        req.addProperty("mode", "damage");
        req.addProperty("title", "Assign combat damage from " + strip(attacker.getName()));
        req.addProperty("total", damage);
        req.addProperty("order", order);
        req.addProperty("free", free);
        req.addProperty("may_skip", maySkip);
        cardIndex.put(attacker.getId(), attacker);
        req.add("source", Snapshot.card(attacker, me(), null));
        req.add("rows", rows);
        JsonElement reply = wire.request(req);
        if (reply == null || !reply.isJsonArray()) {
            if (maySkip && reply != null && reply.isJsonPrimitive() && !reply.getAsBoolean()) {
                return null;                        // the player chose to decide this attacker later
            }
        } else {
            JsonArray a = reply.getAsJsonArray();
            int[] got = new int[a.size()];
            for (int i = 0; i < got.length; i++) got[i] = a.get(i).getAsInt();
            String problem = Assign.checkDamage(got, damage, lethal, order, free, defenderRow);
            if (problem == null) {
                amounts = got;
            } else {
                Checks.unusable("assign_damage", "reply refused (" + problem + "); used the automatic split");
            }
        }
        for (int i = 0; i < bl.size(); i++) result.put(bl.get(i), amounts[i]);
        if (defenderRow) result.put(null, amounts[n - 1]);
        return result;
    }

    @Override
    public Map<Object, Integer> assignGenericAmount(CardView effectSource, Map<Object, Integer> target, int amount,
                                                    boolean atLeastOne, String amountLabel) {
        // "Divide N damage / counters / shield among these", and "add N mana in any combination of these colours".
        List<Object> keys = new ArrayList<>(target.keySet());
        int n = keys.size();
        Map<Object, Integer> result = new HashMap<>();
        if (n == 0) return result;
        int[] max = new int[n];
        JsonArray rows = new JsonArray();
        for (int i = 0; i < n; i++) {
            Object k = keys.get(i);
            Integer m = target.get(k);
            max[i] = m != null ? m : amount;
            JsonObject row;
            if (k instanceof GameEntityView) {
                row = entity((GameEntityView) k);
            } else if (k instanceof MagicColor.Color) {
                MagicColor.Color col = (MagicColor.Color) k;
                row = new JsonObject();
                row.addProperty("kind", "mana");
                row.addProperty("symbol", col.getShortName());
                row.addProperty("name", col.getName());
            } else {
                row = new JsonObject();
                row.addProperty("kind", "other");
                row.addProperty("name", String.valueOf(k));
            }
            row.addProperty("max", max[i]);
            rows.add(row);
        }
        int[] amounts = Assign.autoDivide(amount, max, atLeastOne);
        JsonObject req = new JsonObject();
        req.addProperty("kind", "assign");
        req.addProperty("mode", "divide");
        String label = amountLabel == null ? "amount" : strip(amountLabel);
        req.addProperty("title", "Divide " + amount + " " + label + (effectSource != null ? " (" + strip(effectSource.getName()) + ")" : ""));
        req.addProperty("label", label);
        req.addProperty("total", amount);
        req.addProperty("at_least_one", atLeastOne);
        if (effectSource != null) {
            cardIndex.put(effectSource.getId(), effectSource);
            req.add("source", Snapshot.card(effectSource, me(), null));
        }
        req.add("rows", rows);
        JsonElement reply = wire.request(req);
        if (reply != null && reply.isJsonArray()) {
            JsonArray a = reply.getAsJsonArray();
            int[] got = new int[a.size()];
            for (int i = 0; i < got.length; i++) got[i] = a.get(i).getAsInt();
            String problem = Assign.checkDivide(got, amount, max, atLeastOne);
            if (problem == null) {
                amounts = got;
            } else {
                Checks.unusable("assign_divide", "reply refused (" + problem + "); used the automatic split");
            }
        }
        for (int i = 0; i < n; i++) result.put(keys.get(i), amounts[i]);
        return result;
    }

    @Override
    public List<PaperCard> sideboard(CardPool sideboard, CardPool main, String message) {
        return new ArrayList<>();
    }

    @Override
    public void message(String message, String title) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "message");
        m.addProperty("title", strip(title));
        m.addProperty("text", strip(message));
        wire.send(m);
    }

    @Override
    public void showErrorDialog(String message, String title) {
        message(message, title);
    }

    // Round FB1 (Forge fb4d809): tempShowZones / hideZones / restoreOldZones are gone from IGuiGame, and openZones is now a
    // void default. Forge moved "which zones to open while choosing" from the host into each GUI (upstream #12023,
    // "Move zone display decisions from host to client"). This bridge never opened zones (the overrides were no-ops and
    // UI_SELECT_FROM_CARD_DISPLAYS is off, so library / graveyard choices arrive as lists), so Forge's no-op defaults do
    // exactly what the overrides did. showRevealedCards / hideRevealedCards (upstream #12016, a hand reveal during a
    // prompt) are no-op defaults too: the table already shows the revealed cards in the choice itself.
}
