/*
 * This file is a copy of forge-game's forge/game/mulligan/MulliganService.java (Forge, GNU GPL v3, see forge_bundle/FORGE_LICENSE.txt)
 * with ONE change: the first mulligan is free in every game with commander rules, not only in pods and Brawl (see
 * initializeMulligans). It is packed into forge_bridge.jar, which
 * comes before forge.jar on the class path, so this version is the one Forge loads.
 * Modified 2026-09-20 by Commander Sim: the first mulligan is always free.
 * Modified 2026-10-09 by Manticore (round CON1): ... except in a two-player game without commander rules (Constructed).
 */
package forge.game.mulligan;

import java.util.List;

import com.google.common.collect.Lists;

import forge.MulliganDefs;
import forge.StaticData;
import forge.game.Game;
import forge.game.GameType;
import forge.game.player.Player;

public class MulliganService {
    Player firstPlayer;
    Game game;
    List<AbstractMulligan> mulligans = Lists.newArrayList();

    public MulliganService(Player player) {
        firstPlayer = player;
        game = firstPlayer.getGame();
    }

    public void perform() {
        initializeMulligans();
        runPlayerMulligans();
        runPostMulligans();
    }

    private void initializeMulligans() {
        List<Player> whoCanMulligan = Lists.newArrayList(game.getPlayers());
        int offset = whoCanMulligan.indexOf(firstPlayer);

        for (int i = 0; i < offset; i++) {
            whoCanMulligan.add(whoCanMulligan.remove(0));
        }

        // Commander: every player's first mulligan is free (a new seven, nothing put back). Forge only grants that with three or
        // more players or in Brawl, so a two-player game against one AI would not get it.
        // Round CON1 (Manticore, 2026-10-09): only in a game with commander rules (Commander, Brawl) or with more than two players.
        // A two-player Constructed game (Standard) takes every mulligan in full, as the Comprehensive Rules say (103.5c: the
        // first mulligan is free "in a multiplayer game and in any Brawl game").
        boolean firstMullFree = game.getRules().hasCommander() || game.getPlayers().size() > 2;

        for (Player player : whoCanMulligan) {
            MulliganDefs.MulliganRule rule = StaticData.instance().getMulliganRule();
            AbstractMulligan mulligan;
            switch (rule) {
                case Original:
                    mulligan = new OriginalMulligan(player, firstMullFree);
                    break;
                case Paris:
                    mulligan = new ParisMulligan(player, firstMullFree);
                    break;
                case Vancouver:
                    mulligan = new VancouverMulligan(player, firstMullFree);
                    break;
                case London:
                    mulligan = new LondonMulligan(player, firstMullFree);
                    break;
                case Houston:
                    mulligan = new HoustonMulligan(player, firstMullFree);
                    break;
                default:
                    mulligan = new VancouverMulligan(player, firstMullFree);
                    break;
            }

            mulligans.add(mulligan);
            mulligan.beforeFirstMulligan();
        }
    }

    private void runPlayerMulligans() {
        boolean allKept;
        do {
            allKept = true;
            for (AbstractMulligan mulligan : mulligans) {
                if (mulligan.hasKept()) {
                    continue;
                }

                Player p = mulligan.getPlayer();

                boolean keep = !mulligan.canMulligan() ||
                        p.getController().mulliganKeepHand(
                                firstPlayer,
                                mulligan.tuckCardsDuringMulligan()
                        );

                if (game.isGameOver()) {
                    // conceded during mulligan prompt
                    return;
                }

                if (keep) {
                    mulligan.keep();
                    continue;
                }

                allKept = false;
                mulligan.mulligan();
            }
        } while (!allKept);
    }

    private void runPostMulligans() {
        for (AbstractMulligan mulligan : mulligans) {
            mulligan.afterMulligan();
        }
    }
}