// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.common.eventbus.Subscribe;

import forge.card.GamePieceType;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.event.GameEventGameStarted;
import forge.game.player.Player;
import forge.game.zone.PlayerZone;
import forge.game.zone.ZoneType;

import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Round CD3: a cube's own rules that Forge doesn't have (Main --house-rule NAME; house_rules.py on the Python side).
 *
 * turbo - Turbo Cube (cubecobra.com/cube/overview/turbo, read 12 Oct 2026): "Players begin the game with an emblem: All spells and
 *     activated abilities cost {2} less." Each player gets an emblem in their command zone that lowers what THAT player casts and
 *     activates by {2} ("Activator$ You"), so every spell and activated ability in the game costs {2} less, never {4} (two emblems
 *     that each said "all spells" would stack). No "Type$": Forge's CostAdjustment.checkRequirement then accepts spells and
 *     activated abilities alike, and applyReduceCostAbility lowers only the generic part, never below {0} (CR 118.7a, 601.2f).
 *     The static is the same text Forge's own cards use for a cost reducer in the command zone (e.g. "Mode$ ReduceCost |
 *     EffectZone$ Command | ValidCard$ Card | Type$ Spell | Activator$ You | Amount$ 1", res/cardsfolder at fb4d809), minus Type$.
 *
 * When: on GameEventGameStarted, which Forge fires on the game thread at the start of GameAction.startGame - after Match.prepareAllZones
 * (decks, command zones) and before the opening hands. The bridge joins the game's event bus before the game thread starts
 * (BridgeGui.setOriginalGameController), and Forge's game EventBus calls its subscribers on the posting thread, so the emblem is
 * made by the game thread itself (no race with it), before anyone can cast anything, at the same point in every run of the same
 * game (a resumed game and Undo's rebuild give it the same card ids). A game restarted by Karn Liberated fires the event again:
 * a player who already has the emblem doesn't get a second one.
 */
public class HouseRules {
    public static final String TURBO = "turbo";
    public static final String TURBO_NAME = "Turbo Cube Emblem";
    static final String TURBO_STATIC = "Mode$ ReduceCost | EffectZone$ Command | ValidCard$ Card | Activator$ You | Amount$ 2 | "
            + "Description$ Spells you cast and abilities you activate cost {2} less to cast or activate.";

    /** The rules this bridge knows (Main refuses any other --house-rule). */
    public static final Set<String> KNOWN = Set.of(TURBO);

    /** The rules asked for on the command line (Main --house-rule). Empty = an ordinary game. */
    static final Set<String> active = new LinkedHashSet<>();

    private final Game game;

    HouseRules(Game game) {
        this.game = game;
    }

    @Subscribe
    public void started(GameEventGameStarted ev) {
        reapply(game);
    }

    /** Round CD3: put the active rules' emblems back after Forge's Setup Game State (SetupState: a dev set-up or LAST1's restore),
     *  which empties every zone. Called on the game thread. Nothing happens in a game without a house rule. */
    static void reapply(Game game) {
        if (active.contains(TURBO)) {
            for (Player p : game.getPlayers()) {
                addTurboEmblem(game, p);
            }
        }
    }

    /** One Turbo Cube emblem in this player's command zone (none added when they have it already). Returns the emblem. */
    static Card addTurboEmblem(Game game, Player p) {
        PlayerZone com = p.getZone(ZoneType.Command);
        for (Card c : com.getCards()) {
            if (TURBO_NAME.equals(c.getName())) {
                return c;
            }
        }
        Card eff = new Card(game.nextCardId(), game);   // as Forge's own Player.createPlanechaseEffects makes its "Planar Dice"
        eff.setGameTimestamp(game.getNextTimestamp());
        eff.setName(TURBO_NAME);
        eff.setOwner(p);
        eff.setGamePieceType(GamePieceType.EFFECT);
        eff.addStaticAbility(TURBO_STATIC);
        com.add(eff);
        // Not "bridge:" - card_check.PROBLEM_LINE and the soak's engine_error rule count those lines as problems (round 29a).
        System.err.println("houserules: turbo: " + p.getName() + " has the Turbo Cube emblem (card " + eff.getId() + ")");
        return eff;
    }
}
