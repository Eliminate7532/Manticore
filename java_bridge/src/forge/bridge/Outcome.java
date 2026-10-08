// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.util.ArrayList;
import java.util.List;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameOutcome;
import forge.game.player.Player;
import forge.game.player.PlayerOutcome;
import forge.game.player.PlayerStatistics;

/**
 * Patch 44 (stats, Karl's decisions of 6 Oct 2026): how the game ended, from Forge's own records, sent with "game_over" as
 * "result". The table's stats (stats.py) use it for "when and how games end": each player's seat in turn order (the d20 seat),
 * how many turns they took, and why they lost (life, commander damage, poison, an empty library, a "you lose" card, an
 * opponent's alternate win, a concede) or which card won it for them (Thassa's Oracle and the like).
 *
 * <pre>
 * {"endReason": "AllOpponentsLost" | "WinsGameSpellEffect" | "Draw" | ..., "winSpell": "...", "lastTurn": 25, "draw": false,
 *  "players": [{"id", "name", "seat": 1-4, "won": true|false, "loss": "LifeReachedZero" | "CommanderDamage" | "Poisoned" |
 *               "Milled" | "SpellEffect" | "OpponentWon" | "Conceded" | "IntentionalDraw", "lossSpell", "altWin",
 *               "turns", "mulligans"}]}
 * </pre>
 * Everything in it is public information. Never throws: a field Forge can't give is left out ("error" says why).
 */
final class Outcome {
    private Outcome() {
    }

    static JsonObject describe(Game g) {
        JsonObject o = new JsonObject();
        if (g == null) {
            return o;
        }
        try {
            GameOutcome go = g.getOutcome();
            if (go != null) {
                if (go.getWinCondition() != null) {
                    o.addProperty("endReason", go.getWinCondition().name());
                }
                if (go.getWinSpellEffect() != null) {
                    o.addProperty("winSpell", go.getWinSpellEffect());
                }
                o.addProperty("lastTurn", go.getLastTurnNumber());
                o.addProperty("draw", go.isDraw());
            }
            // Turns pass in the order of the registered players, starting from the starting player.
            List<Player> order = new ArrayList<>(g.getRegisteredPlayers());
            Player start = g.getStartingPlayer();
            int s = start == null ? -1 : order.indexOf(start);
            int n = order.size();
            JsonArray players = new JsonArray();
            for (int i = 0; i < n; i++) {
                Player p = order.get(i);
                JsonObject x = new JsonObject();
                x.addProperty("id", p.getId());
                x.addProperty("name", p.getName());
                if (s >= 0) {
                    x.addProperty("seat", ((i - s) % n + n) % n + 1);
                }
                PlayerOutcome po = p.getOutcome();
                if (po != null) {
                    x.addProperty("won", po.hasWon());
                    if (po.lossState != null) {
                        x.addProperty("loss", po.lossState.name());
                    }
                    if (po.loseConditionSpell != null) {
                        x.addProperty("lossSpell", po.loseConditionSpell);
                    }
                    if (po.altWinSourceName != null) {
                        x.addProperty("altWin", po.altWinSourceName);
                    }
                }
                PlayerStatistics st = p.getStats();
                if (st != null) {
                    x.addProperty("turns", st.getTurnsPlayed());
                    x.addProperty("mulligans", st.getMulliganCount());
                }
                players.add(x);
            }
            o.add("players", players);
        } catch (RuntimeException e) {
            o.addProperty("error", e.toString());
        }
        return o;
    }
}
