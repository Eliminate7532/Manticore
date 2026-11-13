// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.item.IPaperCard;
import forge.model.FModel;
import forge.game.player.RegisteredPlayer;

import java.util.ArrayList;
import java.util.List;

/**
 * Round PROG3: one seat's start effects (Main --start-effects SPEC; start_effects.py on the Python side): what the Adventure's
 * levels, perks and (PROG4) gear change at the start of a duel - for the player's seat only, never a foe's.
 *
 * SPEC is "key:value" items joined by ';' (no card name has a ';'; a value may hold ':' - "Circle of Protection: Red"):
 *   life:N          starting life (1-999)
 *   hand:N          opening-hand size (0-20)
 *   battlefield:X   a card named X on the battlefield at the start (repeat the item for more); "token:SCRIPT" is one of Forge's
 *                   token scripts (res/tokenscripts, e.g. token:c_a_treasure_sac = a Treasure)
 *   command:X       a card named X in the command zone at the start (an emblem-like relic)
 *
 * How (checked in Forge 9a6f776 with javap, 3 Nov 2026): Forge already has these, for its own Adventure duels - RegisteredPlayer
 * setStartingLife (Game's constructor gives it to Player.setStartingLife when no life override is passed: Match.createGame passes
 * -1), setStartingHand (Player.setStartingHandSize and setMaxHandSize - so the hand-size limit moves with it, as in Forge's
 * Adventure), addExtraCardsOnBattlefield and addExtraCardsInCommandZone (Player.initVariantsZones, called by Match.prepareAllZones
 * when the game starts, before the opening hands; the cards come in with summoning sickness, not collectible). So this class only
 * reads SPEC and fills the RegisteredPlayer - Forge applies it, in every run of the same game the same way (Resume, Undo's rebuild
 * and a bug report's replay start from the same .dck metadata, "Manticore Start=").
 *
 * java_rpg's forge.rpgtools.Duels has the same parser (its own copy: rpg_tools.jar doesn't load the bridge); tests/test_prog3.py
 * checks the two parse() bodies are the same text.
 */
public final class StartEffects {
    public Integer life;
    public Integer hand;
    public final List<String> battlefield = new ArrayList<>();
    public final List<String> command = new ArrayList<>();

    /** The effects asked for on the command line (Main --start-effects), applied to the human seat. null = none. */
    static StartEffects active;

    public static StartEffects parse(String spec) {
        StartEffects e = new StartEffects();
        if (spec == null) return e;
        for (String item : spec.split(";")) {
            item = item.trim();
            if (item.isEmpty()) continue;
            int at = item.indexOf(':');
            if (at <= 0) throw new IllegalArgumentException("start effect '" + item + "' is not key:value");
            String key = item.substring(0, at).trim().toLowerCase(), val = item.substring(at + 1).trim();
            switch (key) {
                case "life": e.life = number(key, val, 1, 999); break;
                case "hand": e.hand = number(key, val, 0, 20); break;
                case "battlefield": if (val.isEmpty()) throw new IllegalArgumentException("battlefield: no card named"); e.battlefield.add(val); break;
                case "command": if (val.isEmpty()) throw new IllegalArgumentException("command: no card named"); e.command.add(val); break;
                default: throw new IllegalArgumentException("unknown start effect '" + key + "' (known: life, hand, battlefield, command)");
            }
        }
        return e;
    }

    static int number(String key, String val, int lo, int hi) {
        int n;
        try {
            n = Integer.parseInt(val);
        } catch (NumberFormatException ex) {
            throw new IllegalArgumentException(key + ": '" + val + "' is not a whole number");
        }
        if (n < lo || n > hi) throw new IllegalArgumentException(key + ": " + n + " is not from " + lo + " to " + hi);
        return n;
    }

    public boolean isEmpty() {
        return life == null && hand == null && battlefield.isEmpty() && command.isEmpty();
    }

    /** One of Forge's cards (or "token:SCRIPT"), or an IllegalArgumentException naming it. */
    static IPaperCard card(String name) {
        IPaperCard c;
        if (name.toLowerCase().startsWith("token:")) {
            c = FModel.getMagicDb().getAllTokens().getToken(name.substring(6).trim());
        } else {
            c = FModel.getMagicDb().getCommonCards().getCard(name);
            if (c == null) {
                // Round PROG4: the gear's relics are Conspiracy cards (Sentinel Dispatch, Hold the Perimeter ...); Forge keeps those in
                // its variant card database, not the common one
                c = FModel.getMagicDb().getVariantCards().getCard(name);
            }
        }
        if (c == null) throw new IllegalArgumentException("no card or token named '" + name + "' in this Forge");
        return c;
    }

    /** Fill the seat's RegisteredPlayer (Forge does the rest when the game starts). Unknown cards: IllegalArgumentException. */
    public void applyTo(RegisteredPlayer rp) {
        if (life != null) rp.setStartingLife(life);
        if (hand != null) rp.setStartingHand(hand);
        List<IPaperCard> bf = new ArrayList<>(), cz = new ArrayList<>();
        for (String n : battlefield) bf.add(card(n));
        for (String n : command) cz.add(card(n));
        if (!bf.isEmpty()) rp.addExtraCardsOnBattlefield(bf);
        if (!cz.isEmpty()) rp.addExtraCardsInCommandZone(cz);
        // Not "bridge:" - card_check.PROBLEM_LINE and the soak's engine_error rule count those lines as problems (round 29a).
        System.err.println("starteffects: " + this);
    }

    /** For the "ready" line: {"life", "hand", "battlefield": [...], "command": [...]} with only what was asked for ({} = none). */
    public JsonObject toJson() {
        JsonObject o = new JsonObject();
        if (life != null) o.addProperty("life", life);
        if (hand != null) o.addProperty("hand", hand);
        if (!battlefield.isEmpty()) {
            JsonArray a = new JsonArray();
            for (String n : battlefield) a.add(n);
            o.add("battlefield", a);
        }
        if (!command.isEmpty()) {
            JsonArray a = new JsonArray();
            for (String n : command) a.add(n);
            o.add("command", a);
        }
        return o;
    }

    @Override
    public String toString() {
        return toJson().toString();
    }
}
