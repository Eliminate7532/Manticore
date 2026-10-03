// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.common.collect.Multimap;
import com.google.common.eventbus.Subscribe;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.GameEntityView;
import forge.game.card.CardView;
import forge.game.event.GameEvent;
import forge.game.event.GameEventAttackersDeclared;
import forge.game.event.GameEventBlockersDeclared;
import forge.game.event.GameEventCardAttachment;
import forge.game.event.GameEventCardChangeZone;
import forge.game.event.GameEventCardCounters;
import forge.game.event.GameEventCardDamaged;
import forge.game.event.GameEventCardSacrificed;
import forge.game.event.GameEventCardTapped;
import forge.game.event.GameEventFlipCoin;
import forge.game.event.GameEventGameOutcome;
import forge.game.event.GameEventLandPlayed;
import forge.game.event.GameEventMulligan;
import forge.game.event.GameEventPlayerDamaged;
import forge.game.event.GameEventPlayerLivesChanged;
import forge.game.event.GameEventPlayerPoisoned;
import forge.game.event.GameEventRollDie;
import forge.game.event.GameEventShuffle;
import forge.game.event.GameEventSnapshotRestored;
import forge.game.event.GameEventSpellAbilityCast;
import forge.game.event.GameEventSpellResolved;
import forge.game.event.GameEventTokenCreated;
import forge.game.event.GameEventTurnBegan;
import forge.game.event.GameEventTurnPhase;
import forge.game.player.PlayerView;
import forge.game.spellability.SpellAbilityView;
import forge.game.zone.ZoneView;

import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Sends a short list of Forge's game events to the client as {"t":"event","seq":N,"kind":...} lines.
 * The table uses them for sounds and animations (what moved from where to where), instead of guessing by
 * comparing two snapshots. Every event gets a number (seq); every snapshot says the newest number it already
 * includes ("eventSeq"), so the table can wait for the board to show the result before animating it.
 * Registered on the game's event bus by BridgeGui.setOriginalGameController (before the game thread starts).
 */
public class EventForwarder {
    private final Wire wire;
    private final AtomicLong seq = new AtomicLong(0);

    public EventForwarder(Wire wire) {
        this.wire = wire;
    }

    /** The newest event number sent so far (read BEFORE a snapshot is built, so the snapshot includes at least these). */
    public long lastSeq() {
        return seq.get();
    }

    @Subscribe
    public synchronized void receive(GameEvent ev) {
        JsonObject o;
        try {
            o = describe(ev);
        } catch (RuntimeException e) {          // an event we could not describe must never stop the game
            System.err.println("bridge: event " + ev.getClass().getSimpleName() + " not sent: " + e);
            return;
        }
        if (o == null) {
            return;
        }
        o.addProperty("t", "event");
        o.addProperty("seq", seq.incrementAndGet());
        wire.send(o);
    }

    private static JsonObject kind(String k) {
        JsonObject o = new JsonObject();
        o.addProperty("kind", k);
        return o;
    }

    private static void card(JsonObject o, String key, CardView c) {
        if (c != null) o.addProperty(key, c.getId());
    }

    private static void player(JsonObject o, String key, PlayerView p) {
        if (p != null) o.addProperty(key, p.getId());
    }

    /** "c12" for a card, "p3" for a player (the same spelling as stack targets in the snapshot). */
    private static String ref(GameEntityView e) {
        if (e instanceof CardView) return "c" + e.getId();
        if (e instanceof PlayerView) return "p" + e.getId();
        return e == null ? "" : "?" + e.getId();
    }

    static JsonObject describe(GameEvent ev) {
        if (ev instanceof GameEventCardChangeZone e) {
            JsonObject o = kind("zone");
            card(o, "card", e.card());
            ZoneView from = e.from(), to = e.to();
            if (from != null) {
                o.addProperty("from", from.zoneType().name());
                player(o, "fromPlayer", from.player());
            }
            if (to != null) {
                o.addProperty("to", to.zoneType().name());
                player(o, "toPlayer", to.player());
            }
            return o;
        }
        if (ev instanceof GameEventCardTapped e) {
            JsonObject o = kind("tap");
            card(o, "card", e.card());
            o.addProperty("tapped", e.tapped());
            return o;
        }
        if (ev instanceof GameEventCardDamaged e) {
            JsonObject o = kind("damage_card");
            card(o, "card", e.card());
            card(o, "source", e.source());
            o.addProperty("amount", e.amount());
            return o;
        }
        if (ev instanceof GameEventPlayerDamaged e) {
            JsonObject o = kind("damage_player");
            player(o, "player", e.target());
            card(o, "source", e.source());
            o.addProperty("amount", e.amount());
            o.addProperty("combat", e.combat());
            return o;
        }
        if (ev instanceof GameEventPlayerLivesChanged e) {
            JsonObject o = kind("life");
            player(o, "player", e.player());
            o.addProperty("old", e.oldLives());
            o.addProperty("new", e.newLives());
            return o;
        }
        if (ev instanceof GameEventPlayerPoisoned e) {
            JsonObject o = kind("poison");
            player(o, "player", e.receiver());
            o.addProperty("amount", e.amount());
            return o;
        }
        if (ev instanceof GameEventSpellAbilityCast e) {
            JsonObject o = kind("cast");
            SpellAbilityView sa = e.sa();
            if (sa != null) {
                card(o, "card", sa.getHostCard());
            }
            if (e.si() != null) {
                o.addProperty("trigger", e.si().isTrigger());
                o.addProperty("ability", e.si().isAbility());
                player(o, "player", e.si().getActivatingPlayer());
            }
            o.addProperty("stackIndex", e.stackIndex());
            return o;
        }
        if (ev instanceof GameEventSpellResolved e) {
            JsonObject o = kind("resolve");
            if (e.spell() != null) card(o, "card", e.spell().getHostCard());
            o.addProperty("fizzled", e.hasFizzled());
            return o;
        }
        if (ev instanceof GameEventCardCounters e) {
            JsonObject o = kind("counters");
            card(o, "card", e.card());
            o.addProperty("counter", String.valueOf(e.type()));
            o.addProperty("old", e.oldValue());
            o.addProperty("new", e.newValue());
            return o;
        }
        if (ev instanceof GameEventCardSacrificed e) {
            JsonObject o = kind("sacrifice");
            card(o, "card", e.card());
            return o;
        }
        if (ev instanceof GameEventCardAttachment e) {
            JsonObject o = kind("attach");
            card(o, "card", e.equipment());
            if (e.newTarget() != null) o.addProperty("to", ref(e.newTarget()));
            return o;
        }
        if (ev instanceof GameEventLandPlayed e) {
            JsonObject o = kind("land");
            player(o, "player", e.player());
            card(o, "card", e.land());
            return o;
        }
        if (ev instanceof GameEventAttackersDeclared e) {
            JsonObject o = kind("attack");
            player(o, "player", e.player());
            JsonArray a = new JsonArray();
            Multimap<GameEntityView, CardView> m = e.attackersMap();
            if (m != null) {
                for (Map.Entry<GameEntityView, CardView> en : m.entries()) {
                    JsonObject x = new JsonObject();
                    x.addProperty("card", en.getValue().getId());
                    x.addProperty("defender", ref(en.getKey()));
                    a.add(x);
                }
            }
            o.add("attackers", a);
            return o;
        }
        if (ev instanceof GameEventBlockersDeclared e) {
            JsonObject o = kind("block");
            player(o, "player", e.defendingPlayer());
            JsonArray a = new JsonArray();
            Map<GameEntityView, Multimap<CardView, CardView>> m = e.blockers();
            if (m != null) {
                for (Multimap<CardView, CardView> mm : m.values()) {
                    for (Map.Entry<CardView, CardView> en : mm.entries()) {
                        // Checked live on 2026-09-23 (tests/test_round22.py): the key is the ATTACKER, the value its BLOCKER, and an
                        // attacker nobody blocked appears paired with itself; those are left out.
                        if (en.getKey().getId() == en.getValue().getId()) {
                            continue;
                        }
                        JsonObject x = new JsonObject();
                        x.addProperty("attacker", en.getKey().getId());
                        x.addProperty("blocker", en.getValue().getId());
                        a.add(x);
                    }
                }
            }
            o.add("pairs", a);
            return o;
        }
        if (ev instanceof GameEventTurnBegan e) {
            JsonObject o = kind("turn");
            player(o, "player", e.turnOwner());
            o.addProperty("turn", e.turnNumber());
            return o;
        }
        if (ev instanceof GameEventTurnPhase e) {
            JsonObject o = kind("phase");
            player(o, "player", e.playerTurn());
            if (e.phase() != null) o.addProperty("phase", e.phase().name());
            return o;
        }
        if (ev instanceof GameEventShuffle e) {
            JsonObject o = kind("shuffle");
            player(o, "player", e.player());
            return o;
        }
        if (ev instanceof GameEventMulligan e) {
            JsonObject o = kind("mulligan");
            player(o, "player", e.player());
            return o;
        }
        if (ev instanceof GameEventTokenCreated) return kind("token");
        if (ev instanceof GameEventFlipCoin) return kind("coin");
        if (ev instanceof GameEventRollDie) return kind("die");
        if (ev instanceof GameEventSnapshotRestored e) {
            JsonObject o = kind("rewind");
            o.addProperty("start", e.start());
            return o;
        }
        if (ev instanceof GameEventGameOutcome e) {
            JsonObject o = kind("outcome");
            if (e.winningPlayerName() != null) o.addProperty("winner", e.winningPlayerName());
            return o;
        }
        return null;                                // every other event: not needed by the table
    }
}
