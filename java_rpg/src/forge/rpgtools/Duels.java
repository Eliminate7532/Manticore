// SPDX-License-Identifier: GPL-3.0-or-later
package forge.rpgtools;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import forge.GuiDesktop;
import forge.deck.Deck;
import forge.deck.io.DeckSerializer;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameOutcome;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.player.GamePlayerUtil;
import forge.util.MyRandom;
import forge.view.TimeLimitedCodeBlock;

/**
 * Round RPG9: Forge's AI against Forge's AI, many duels in one Java start, for the RPG's balance (developer tool, like RpgData;
 * NOT part of the game bridge, so building it never changes forge_bridge.jar's source stamp). It does what Forge's own
 * "forge.exe sim" does for one match (forge.view.SimulateMatch.simulateSingleMatch: Match.createGame, setNoGUIUser, startGame
 * under a time limit, a game over the limit is a draw), but for a list of pairings, so Forge's model (about 25 s to start) is
 * loaded once:
 *
 *   java -cp rpg_tools.jar:forge.jar forge.rpgtools.Duels PAIRS.tsv OUT.jsonl [TIMEOUT_SECONDS]
 *
 * PAIRS.tsv: one pairing a line, "id TAB deckA.dck TAB deckB.dck TAB games TAB seed". The two seats are named "A" and "B" (their
 * own AI each, Forge's default profile), 20 life, Forge's Constructed rules - what the RPG's duels play (formats "standard").
 * OUT.jsonl gets one line a game: {"id", "game", "winner": "A"|"B"|"draw", "first": "A"|"B"|null, "turns", "ms"}, written and
 * flushed as each game ends, so a run that is stopped keeps what it played.
 */
public final class Duels {

    private Duels() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: Duels PAIRS.tsv OUT.jsonl [TIMEOUT_SECONDS]");
            System.exit(2);
        }
        int timeout = args.length > 2 ? Integer.parseInt(args[2]) : 120;
        System.setProperty("java.awt.headless", "true");
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, null);
        FModel.getPreferences().setPref(FPref.UI_ENABLE_SOUNDS, false);
        FModel.getPreferences().setPref(FPref.UI_ENABLE_MUSIC, false);
        List<String[]> pairs = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(args[0]), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (!line.isBlank() && !line.startsWith("#")) {
                    pairs.add(line.split("\t"));
                }
            }
        }
        try (Writer w = new OutputStreamWriter(new FileOutputStream(args[1], true), StandardCharsets.UTF_8)) {
            for (String[] p : pairs) {
                play(w, p[0], p[1], p[2], Integer.parseInt(p[3]), Long.parseLong(p[4]), timeout);
            }
        }
        System.exit(0);                  // Forge's model leaves non-daemon threads behind
    }

    static void play(Writer w, String id, String a, String b, int games, long seed, int timeout) throws Exception {
        Deck da = DeckSerializer.fromFile(new File(a));
        Deck db = DeckSerializer.fromFile(new File(b));
        if (da == null || db == null) {
            w.write("{\"id\": " + RpgData.q(id) + ", \"error\": " + RpgData.q("could not read " + (da == null ? a : b)) + "}\n");
            w.flush();
            return;
        }
        MyRandom.setRandom(new Random(seed));
        List<RegisteredPlayer> players = new ArrayList<>();
        RegisteredPlayer ra = new RegisteredPlayer(da);
        ra.setPlayer(GamePlayerUtil.createAiPlayer("A", 0));
        RegisteredPlayer rb = new RegisteredPlayer(db);
        rb.setPlayer(GamePlayerUtil.createAiPlayer("B", 1));
        players.add(ra);
        players.add(rb);
        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(games);
        Match match = new Match(rules, players, "RPG balance");
        for (int g = 1; g <= games; g++) {
            long t0 = System.currentTimeMillis();
            Game game = match.createGame();
            game.setNoGUIUser();
            try {
                TimeLimitedCodeBlock.runWithTimeout(() -> match.startGame(game), timeout, TimeUnit.SECONDS);
            } catch (TimeoutException e) {
                game.setGameOver(GameEndReason.Draw);
            } catch (Exception e) {
                e.printStackTrace();
                game.setGameOver(GameEndReason.Draw);
            }
            GameOutcome o = game.getOutcome();
            String winner = "draw";
            if (o != null && !o.isDraw() && o.getWinningLobbyPlayer() != null) {
                winner = o.getWinningLobbyPlayer().getName();
            }
            Player first = game.getStartingPlayer();
            String firstName = first == null ? null : first.getName();
            int turns = o == null ? 0 : o.getLastTurnNumber();
            w.write("{\"id\": " + RpgData.q(id) + ", \"game\": " + g + ", \"winner\": " + RpgData.q(winner) + ", \"first\": "
                    + (firstName == null ? "null" : RpgData.q(firstName)) + ", \"turns\": " + turns + ", \"ms\": "
                    + (System.currentTimeMillis() - t0) + "}\n");
            w.flush();
        }
    }
}
