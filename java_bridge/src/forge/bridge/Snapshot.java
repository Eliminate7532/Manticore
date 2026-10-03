package forge.bridge;

import com.google.common.collect.Multiset;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.card.ColorSet;
import forge.card.MagicColor;
import forge.card.mana.ManaAtom;
import forge.game.GameEntityView;
import forge.game.GameView;
import forge.game.card.CardView;
import forge.game.card.CardView.CardStateView;
import forge.game.card.CounterType;
import forge.game.combat.CombatView;
import forge.game.player.PlayerView;
import forge.game.spellability.StackItemView;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.YieldController;
import forge.interfaces.IGameController;

import java.util.Map;

/** Turns Forge's view objects (what its own GUI reads) into JSON for the client. */
public final class Snapshot {
    private Snapshot() {
    }

    static String mana(forge.card.mana.ManaCost cost) {
        if (cost == null || cost.isNoCost()) {
            return "";
        }
        return cost.toString();
    }

    static JsonArray colors(ColorSet cs) {
        JsonArray a = new JsonArray();
        if (cs == null) {
            return a;
        }
        if (cs.hasWhite()) a.add("W");
        if (cs.hasBlue()) a.add("U");
        if (cs.hasBlack()) a.add("B");
        if (cs.hasRed()) a.add("R");
        if (cs.hasGreen()) a.add("G");
        return a;
    }

    /** Round MP2: true while a spectator's snapshot is being built on this thread (see build). */
    private static final ThreadLocal<Boolean> SPECTATOR = ThreadLocal.withInitial(() -> false);

    /** Round MP2: what a spectator may see - nothing that is hidden from every player at the table: cards in a hidden zone
     *  (hands, libraries) and face-down cards anywhere. Deliberately stricter than any one player's view. */
    static boolean shownToSpectator(CardView c) {
        if (c.isFaceDown()) {
            return false;
        }
        ZoneType z = c.getZone();
        return z == null || !z.isHidden();
    }

    static JsonObject card(CardView c, PlayerView viewer, BridgeGui gui) {
        JsonObject o = new JsonObject();
        o.addProperty("id", c.getId());
        boolean shown = viewer != null ? c.canBeShownTo(viewer) : !SPECTATOR.get() || shownToSpectator(c);
        o.addProperty("hidden", !shown);
        if (c.getOwner() != null) o.addProperty("owner", c.getOwner().getId());
        if (c.getController() != null) o.addProperty("controller", c.getController().getId());
        ZoneType z = c.getZone();
        if (z != null) o.addProperty("zone", z.name());
        if (!shown) {
            o.addProperty("name", "Face-down card");
            return o;
        }
        CardStateView st = c.getCurrentState();
        if (st != null) {
            o.addProperty("name", st.getName());
            o.addProperty("oracleName", st.getOracleName());
            o.addProperty("type", st.getType() == null ? "" : st.getType().toString());
            o.addProperty("cost", mana(st.getManaCost()));
            o.add("colors", colors(st.getColors()));
            o.addProperty("text", st.getOracleText());
            o.addProperty("isCreature", st.isCreature());
            o.addProperty("isLand", st.isLand());
            o.addProperty("isPlaneswalker", st.isPlaneswalker());
            if (st.isCreature() || st.hasPrintedPT()) {
                o.addProperty("power", st.getPower());
                o.addProperty("toughness", st.getToughness());
            }
            if (st.isPlaneswalker()) {
                o.addProperty("loyalty", st.getLoyalty());
            }
            String set = st.getSetCode();
            if (set != null) o.addProperty("set", set);
            // The keywords the permanent has RIGHT NOW: printed ones plus anything granted (Jump, an Equipment, an Aura, "until end of turn").
            // Only on the battlefield, to keep every snapshot small. The table flags the ones the card's own text does not mention.
            if (z == ZoneType.Battlefield && st.getKeywords() != null && !st.getKeywords().isEmpty()) {
                JsonArray ka = new JsonArray();
                for (forge.game.keyword.KeywordView k : st.getKeywords()) {
                    String t = k.title();
                    if (t != null && !t.isEmpty()) ka.add(t);
                }
                if (ka.size() > 0) o.add("keywords", ka);
            }
        }
        o.addProperty("tapped", c.isTapped());
        o.addProperty("sick", c.isSick());
        o.addProperty("attacking", c.isAttacking());
        o.addProperty("blocking", c.isBlocking());
        o.addProperty("faceDown", c.isFaceDown());
        o.addProperty("token", c.isToken());
        o.addProperty("commander", c.isCommander());
        o.addProperty("damage", c.getDamage());
        Multiset<CounterType> cs = c.getCounters();
        if (cs != null && !cs.isEmpty()) {
            JsonObject co = new JsonObject();
            for (Multiset.Entry<CounterType> e : cs.entrySet()) {
                co.addProperty(e.getElement().toString(), e.getCount());
            }
            o.add("counters", co);
        }
        CardView att = c.getAttachedTo();
        if (att != null) o.addProperty("attachedTo", att.getId());
        // Imprint (Chrome Mox, Isochron Scepter ...): which cards are exiled with this one. Chrome Mox makes only the colours of the
        // imprinted card, and nothing on the table said so (bug report 2026-09-20 16:14: a blue card was imprinted and {G} was unpayable).
        forge.util.collect.FCollectionView<CardView> imprinted = c.getImprintedCards();
        if (imprinted != null && !imprinted.isEmpty()) {
            JsonArray ia = new JsonArray();
            for (CardView i : imprinted) {
                JsonObject io = new JsonObject();
                boolean visible = viewer != null ? i.canBeShownTo(viewer) : !SPECTATOR.get() || shownToSpectator(i);
                io.addProperty("name", visible ? i.getName() : "a face-down card");
                if (visible && i.getCurrentState() != null) io.add("colors", colors(i.getCurrentState().getColors()));
                ia.add(io);
            }
            o.add("imprinted", ia);
        }
        if (gui != null) {
            o.addProperty("selectable", gui.isSelectable(c));
            int weak;
            try {
                weak = gui.getWeakSelectableStrength(c);
            } catch (RuntimeException e) {
                // Round 28bb: Forge's "you could act with this" list is changed by the game thread while this snapshot is
                // built on another; reading it at that moment threw ArrayIndexOutOfBoundsException and the whole snapshot was
                // lost (soak night 1, game 99). Skip the glow this once; the next snapshot has it.
                weak = 0;
            }
            if (weak > 0) o.addProperty("weak", weak);           // Forge's "you could act with this" glow
            if (gui.isHighlighted(c)) o.addProperty("highlight", true);
        }
        return o;
    }

    private static JsonArray cards(Iterable<CardView> list, PlayerView viewer, BridgeGui gui, Map<Integer, CardView> index) {
        JsonArray a = new JsonArray();
        if (list == null) {
            return a;
        }
        for (CardView c : list) {
            index.put(c.getId(), c);
            a.add(card(c, viewer, gui));
        }
        return a;
    }

    static int count(Iterable<CardView> list) {
        int n = 0;
        if (list != null) {
            for (CardView ignored : list) n++;
        }
        return n;
    }

    /** W, U, B, R, G or C for one of ManaAtom's mana types. */
    static String manaSymbol(byte manaAtom) {
        return manaAtom == ManaAtom.COLORLESS ? "C" : MagicColor.toShortString(manaAtom);
    }

    /** The reverse of manaSymbol: ManaAtom's bit for W, U, B, R, G or C (0 when the text is none of them). */
    static byte manaAtom(String symbol) {
        switch (symbol == null ? "" : symbol.toUpperCase()) {
            case "W": return ManaAtom.WHITE;
            case "U": return ManaAtom.BLUE;
            case "B": return ManaAtom.BLACK;
            case "R": return ManaAtom.RED;
            case "G": return ManaAtom.GREEN;
            case "C": return ManaAtom.COLORLESS;
            default: return 0;
        }
    }

    static JsonObject player(PlayerView p, PlayerView me, BridgeGui gui, Map<Integer, CardView> index) {
        JsonObject o = new JsonObject();
        o.addProperty("id", p.getId());
        o.addProperty("name", p.getName());
        o.addProperty("ai", p.isAI());
        o.addProperty("life", p.getLife());
        o.addProperty("lost", p.getHasLost());
        java.util.Map<Integer, Boolean> choices = gui.playerChoices();        // round 28d: "choose a player" list questions
        if (choices != null && choices.containsKey(p.getId())) {
            o.addProperty("selectable", true);
            if (choices.get(p.getId())) o.addProperty("highlight", true);
        }
        o.addProperty("priority", p.getHasPriority());
        o.addProperty("landsPlayed", p.getNumLandThisTurn());
        o.addProperty("maxLandPlay", p.getMaxLandPlay());
        int poison = p.getCounters(forge.game.card.CounterEnumType.POISON);
        if (poison > 0) o.addProperty("poison", poison);
        // Floating mana. The pool is keyed by ManaAtom's bits: colourless is ManaAtom.COLORLESS (32), NOT MagicColor.COLORLESS (0), so
        // asking with the MagicColor value always answered 0 and colourless mana (Basalt Monolith, Sol Ring...) never showed.
        JsonObject pool = new JsonObject();
        for (byte col : ManaAtom.MANATYPES) {
            int n = p.getMana(col);
            if (n > 0) pool.addProperty(manaSymbol(col), n);
        }
        o.add("manaPool", pool);
        boolean mine = p.equals(me);
        JsonObject zones = new JsonObject();
        zones.add("battlefield", cards(p.getBattlefield(), me, gui, index));
        zones.add("graveyard", cards(p.getGraveyard(), me, gui, index));
        zones.add("exile", cards(p.getExile(), me, gui, index));
        zones.add("command", cards(p.getCommand(), me, gui, index));
        if (mine) {
            zones.add("hand", cards(p.getHand(), me, gui, index));
        }
        o.add("zones", zones);
        o.addProperty("handCount", count(p.getHand()));
        o.addProperty("libraryCount", count(p.getLibrary()));
        JsonArray cmdrs = new JsonArray();
        JsonObject cmdDamage = new JsonObject();
        if (p.getCommanders() != null) {
            for (CardView c : p.getCommanders()) {
                cmdrs.add(c.getId());
                index.put(c.getId(), c);
            }
        }
        o.add("commanders", cmdrs);
        // damage this player has taken from each opposing commander
        for (PlayerView opp : p.getOpponents()) {
            if (opp.getCommanders() == null) continue;
            for (CardView c : opp.getCommanders()) {
                int d = p.getCommanderDamage(c);
                if (d > 0) cmdDamage.addProperty(String.valueOf(c.getId()), d);
            }
        }
        o.add("commanderDamage", cmdDamage);
        if (p.getCommanders() != null) {
            JsonObject casts = new JsonObject();
            for (CardView c : p.getCommanders()) {
                casts.addProperty(String.valueOf(c.getId()), p.getCommanderCast(c));
            }
            o.add("commanderCasts", casts);
        }
        return o;
    }

    /** What Forge is doing on my behalf right now: {mode: turn|stack|until|null, phase, autoPass, autoYields: [ability texts], autoTriggers: {text: accept|decline}}. */
    static JsonObject yieldState(BridgeGui gui) {
        JsonObject y = new JsonObject();
        IGameController gc = gui.getGameController();
        if (gc == null) return y;
        YieldController yc = gc.getYieldController();
        if (yc.autoPassUntilEndOfTurn()) y.addProperty("mode", "turn");
        else if (yc.autoPassUntilStackEmpty()) y.addProperty("mode", "stack");
        else if (yc.getAutoPassUntilMarker() != null) {
            y.addProperty("mode", "until");
            y.addProperty("phase", yc.getAutoPassUntilMarker().getPhase().name());
        }
        y.addProperty("autoPass", yc.getBoolPref(forge.localinstance.properties.ForgePreferences.FPref.YIELD_AUTO_PASS_NO_ACTIONS));
        JsonArray auto = new JsonArray();
        for (String k : yc.getAutoYields()) auto.add(k);
        y.add("autoYields", auto);
        JsonObject trig = new JsonObject();
        for (Map.Entry<String, forge.player.AutoYieldStore.TriggerDecision> e : yc.getAutoTriggers()) {
            trig.addProperty(e.getKey(), e.getValue().name().toLowerCase());
        }
        y.add("autoTriggers", trig);
        return y;
    }

    static JsonObject build(GameView gv, PlayerView me, BridgeGui gui, Map<Integer, CardView> index) {
        if (gui == null || !gui.spectator) {
            return buildFor(gv, me, gui, index);
        }
        SPECTATOR.set(true);
        try {
            JsonObject o = buildFor(gv, null, gui, index);
            // The table lays the board out around "me": a spectator sees it from the first seat's side (the host), with no hand.
            o.addProperty("spectator", true);
            for (PlayerView p : gv.getPlayers()) {
                o.addProperty("me", p.getId());
                break;
            }
            return o;
        } finally {
            SPECTATOR.set(false);
        }
    }

    private static JsonObject buildFor(GameView gv, PlayerView me, BridgeGui gui, Map<Integer, CardView> index) {
        JsonObject o = new JsonObject();
        o.addProperty("t", "state");
        o.addProperty("turn", gv.getTurn());
        o.addProperty("phase", gv.getPhase() == null ? "" : gv.getPhase().name());
        o.addProperty("phaseLabel", gv.getPhase() == null ? "" : gv.getPhase().nameForUi);
        PlayerView turn = gv.getPlayerTurn();
        if (turn != null) o.addProperty("activePlayer", turn.getId());
        if (me != null) o.addProperty("me", me.getId());
        o.addProperty("gameOver", gv.isGameOver());
        if (gv.isGameOver() && gv.getWinningPlayerName() != null) {
            o.addProperty("winner", gv.getWinningPlayerName());
        }
        JsonArray players = new JsonArray();
        for (PlayerView p : gv.getPlayers()) {
            players.add(player(p, me, gui, index));
        }
        o.add("players", players);

        JsonArray stack = new JsonArray();
        if (gv.getStack() != null) {
            for (StackItemView si : gv.getStack()) {
                JsonObject s = new JsonObject();
                s.addProperty("key", si.getKey());
                s.addProperty("text", si.getText());
                s.addProperty("trigger", si.isTrigger());
                s.addProperty("ability", si.isAbility());
                IGameController ctl = gui == null || gui.spectator ? null : gui.getGameController();   // a spectator yields nothing
                if (ctl != null && si.getKey() != null && !si.getKey().isEmpty()) {
                    if (si.isAbility() && ctl.shouldAutoYield(si.getKey())) s.addProperty("autoYield", true);
                    if (si.isOptionalTrigger() && si.getActivatingPlayer() != null && me != null && si.getActivatingPlayer().equals(me)) {
                        s.addProperty("optional", true);
                        s.addProperty("decision", ctl.getTriggerDecision(si.getKey()).name().toLowerCase());
                    }
                }
                if (si.getSourceCard() != null) {
                    index.put(si.getSourceCard().getId(), si.getSourceCard());
                    s.add("card", card(si.getSourceCard(), me, null));
                }
                if (si.getActivatingPlayer() != null) s.addProperty("activator", si.getActivatingPlayer().getId());
                JsonArray tg = new JsonArray();
                if (si.getTargetCards() != null) for (CardView c : si.getTargetCards()) tg.add("c" + c.getId());
                if (si.getTargetPlayers() != null) for (PlayerView p : si.getTargetPlayers()) tg.add("p" + p.getId());
                s.add("targets", tg);
                stack.add(s);
            }
        }
        o.add("stack", stack);

        JsonArray combat = new JsonArray();
        CombatView cv = gv.getCombat();
        if (cv != null) {
            for (CardView atk : cv.getAttackers()) {
                JsonObject a = new JsonObject();
                a.addProperty("card", atk.getId());
                GameEntityView d = cv.getDefender(atk);
                if (d instanceof PlayerView) a.addProperty("defender", "p" + d.getId());
                else if (d != null) a.addProperty("defender", "c" + d.getId());
                JsonArray bl = new JsonArray();
                if (cv.getBlockers(atk) != null) for (CardView b : cv.getBlockers(atk)) bl.add(b.getId());
                a.add("blockers", bl);
                combat.add(a);
            }
        }
        o.add("combat", combat);

        if (gui != null) {
            JsonObject prompt = new JsonObject();
            prompt.addProperty("message", gui.promptMessage());
            JsonObject ok = new JsonObject();
            ok.addProperty("label", gui.okLabel);
            ok.addProperty("enabled", gui.okEnabled);
            ok.addProperty("focus", gui.okFocus);
            JsonObject cancel = new JsonObject();
            cancel.addProperty("label", gui.cancelLabel);
            cancel.addProperty("enabled", gui.cancelEnabled);
            prompt.add("ok", ok);
            prompt.add("cancel", cancel);
            prompt.addProperty("selecting", gui.isSelecting());
            prompt.addProperty("selMin", gui.getSelectionMin());
            prompt.addProperty("selMax", gui.getSelectionMax());
            CardView pc = gui.promptCard();
            if (pc != null) {
                // Surveil/scry-style prompts ("top of library or graveyard?") carry the single card
                // being looked at; send it the same shape as confirm()'s "source" so the client can
                // draw a thumbnail next to the text instead of just showing the card's name in prose.
                prompt.add("source", card(pc, gui.me(), null));
            }
            o.add("prompt", prompt);
            JsonObject stops = new JsonObject();
            JsonArray mine = new JsonArray();
            for (forge.game.phase.PhaseType ph : gui.stopsMine) mine.add(ph.name());
            JsonArray theirs = new JsonArray();
            for (forge.game.phase.PhaseType ph : gui.stopsTheirs) theirs.add(ph.name());
            stops.add("mine", mine);
            stops.add("theirs", theirs);
            o.add("stops", stops);
            o.add("yield", gui.spectator ? new JsonObject() : yieldState(gui));   // round MP2: a spectator's controller has no yields
        }
        return o;
    }
}
