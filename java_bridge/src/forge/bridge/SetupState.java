package forge.bridge;

import forge.game.Game;
import forge.game.GameState;

/**
 * Round 28c: Forge's "Setup Game State", with a signal when it has finished.
 *
 * GameState.applyToGame hands the work to another thread (game.getAction().invoke) and returns at once. A click sent
 * before that thread finishes lets the game loop run beside it, and a ConcurrentModificationException kills the game
 * thread (seen in 28bb's live win test, and again in 28c's first mid-game-board soak game: a shock land in the new board
 * asked "pay 2 life?", the bot answered and then passed priority while the set-up was still finishing). The set-up
 * itself can ask questions (a shock land's life payment), so the client can't simply wait for silence: it waits for
 * {"t":"setup_done"}, which this sends once the set-up thread has returned.
 */
class SetupState extends GameState {
    void applyThenSignal(final Game game, final Wire wire) {
        game.getAction().invoke(() -> {
            try {
                applyGameOnThread(game);
            } catch (RuntimeException e) {
                System.err.println("bridge: setup failed: " + e);
            } finally {
                com.google.gson.JsonObject m = new com.google.gson.JsonObject();
                m.addProperty("t", "setup_done");
                wire.send(m);
            }
        });
    }
}
