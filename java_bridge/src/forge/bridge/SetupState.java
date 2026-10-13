package forge.bridge;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameState;
import forge.game.player.Player;

/**
 * Round 28c: Forge's "Setup Game State", with a signal when it has finished.
 *
 * GameState.applyToGame hands the work to another thread (game.getAction().invoke) and returns at once. A click sent
 * before that thread finishes lets the game loop run beside it, and a ConcurrentModificationException kills the game
 * thread (seen in 28bb's live win test, and again in 28c's first mid-game-board soak game: a shock land in the new board
 * asked "pay 2 life?", the bot answered and then passed priority while the set-up was still finishing). The set-up
 * itself can ask questions (a shock land's life payment), so the client can't simply wait for silence: it waits for
 * {"t":"setup_done"}, which this sends once the set-up thread has returned.
 * Round CD3: Forge's set-up empties every zone, the command zones too, so a game with a house rule (Turbo Cube's emblem) gets its
 * emblems back on the same thread before setup_done (HouseRules.reapply; nothing happens in a game without one).
 *
 * Round LAST1c (12 Oct 2026):
 * - A restore (the table's "loadstate") that fails says {"t":"state_load_failed","reason",...} instead of setup_done. It used to
 *   say setup_done after "bridge: setup failed", so the table said "Restored" over a fresh game (LAST1's first live run: a
 *   4-player save taken after a knock-out, "Non-matching number of players, (4 vs. 2)"). "untouched": true when it failed before
 *   anything on the table was changed (the player count is checked here first, as Forge's own check does).
 * - A dev set-up ("setup") still says setup_done whatever happens (the soak and the card check wait for it), now with "ok" and,
 *   when it failed, "reason". Throwable, not only RuntimeException: an Error used to leave the client waiting for nothing.
 * - The designations GameState does not write: The Monarch, The Initiative and the City's Blessing are effect cards with no
 *   paper card in the command zone, and GameState.initFromGame leaves those out (addCard: getPaperCard() == null). Main.saveState
 *   appends them as "#manticore ..." lines (marks()); Forge's own parser skips lines starting with '#' (splitLine), so the file
 *   still loads anywhere. parseLine reads them back and applyMarks puts them on the table once Forge's set-up is done, on the
 *   game thread, the way Forge's own becomeMonarch / takeInitiative / setBlessing do it but without their triggers (the save
 *   was taken after those had run). Not kept (no format for them): hideaway and other cards' own effect cards, emblems, the
 *   dungeon a player is in and its room, the Ring's level beyond what GameState writes.
 */
class SetupState extends GameState {
    static final String MARK = "#manticore ";
    private static final Pattern PLAYER_KEY = Pattern.compile("^(?:p(\\d+)|(human)|(ai))[a-z]");

    String monarch, monarchSet, initiative, initiativeSet;
    final Map<String, String> blessed = new LinkedHashMap<>();
    int playersNamed = -1;                       // the highest player index the file names (p0..pN, human = 0, ai = 1)

    @Override
    protected void parseLine(String line) {
        if (line.startsWith(MARK)) {
            readMark(line.substring(MARK.length()));
            return;
        }
        Matcher m = PLAYER_KEY.matcher(line.toLowerCase());
        if (m.find()) {
            int i = m.group(1) != null ? Integer.parseInt(m.group(1)) : m.group(2) != null ? 0 : 1;
            playersNamed = Math.max(playersNamed, i);
        }
        super.parseLine(line);
    }

    /** "monarch=p1|CN2", "initiative=p0|CLB", "blessing=p2|" - the same value twice (Forge may parse a file twice) changes nothing. */
    void readMark(String text) {
        String[] kv = text.split("=", 2);
        if (kv.length < 2) return;
        String[] v = kv[1].split("\\|", 2);
        String who = v[0].trim().toLowerCase();
        String set = v.length > 1 && !v[1].trim().isEmpty() ? v[1].trim() : null;
        switch (kv[0].trim().toLowerCase()) {
            case "monarch": monarch = who; monarchSet = set; break;
            case "initiative": initiative = who; initiativeSet = set; break;
            case "blessing": blessed.put(who, set); break;
            default: System.err.println("bridge: unknown mark in a saved game: " + text);
        }
    }

    /** The "#manticore" lines for a game as it is now (players numbered as GameState numbers them: game.getPlayers()). */
    static String marks(Game game) {
        StringBuilder sb = new StringBuilder();
        List<Player> ps = game.getPlayers();
        Player m = game.getMonarch();
        if (m != null && ps.contains(m)) {
            sb.append(MARK).append("monarch=p").append(ps.indexOf(m)).append('|').append(orEmpty(m.getMonarchSet())).append('\n');
        }
        Player i = game.getHasInitiative();
        if (i != null && ps.contains(i)) {
            sb.append(MARK).append("initiative=p").append(ps.indexOf(i)).append('|').append(orEmpty(i.getInitiativeSet())).append('\n');
        }
        for (Player p : ps) {
            if (p.hasBlessing()) sb.append(MARK).append("blessing=p").append(ps.indexOf(p)).append("|\n");
        }
        return sb.toString();
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    private static Player player(List<Player> ps, String key) {
        if (key == null || !key.startsWith("p")) return null;
        try {
            int i = Integer.parseInt(key.substring(1));
            return i >= 0 && i < ps.size() ? ps.get(i) : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** On the game thread, after applyGameOnThread. Returns how many designations were put back. */
    int applyMarks(Game game) {
        List<Player> ps = game.getPlayers();
        int n = 0;
        Player m = player(ps, monarch);
        if (m != null) {
            Player before = game.getMonarch();
            if (before != null && before != m) before.removeMonarchEffect();
            m.createMonarchEffect(monarchSet);
            game.setMonarch(m);
            n++;
        }
        Player i = player(ps, initiative);
        if (i != null) {
            Player before = game.getHasInitiative();
            if (before != null && before != i) before.removeInitiativeEffect();
            game.setHasInitiative(i);
            i.createInitiativeEffect(initiativeSet);
            n++;
        }
        for (Map.Entry<String, String> e : blessed.entrySet()) {
            Player b = player(ps, e.getKey());
            if (b != null && !b.hasBlessing()) {
                b.setBlessing(true, e.getValue());
                n++;
            }
        }
        if (n > 0) {
            game.getAction().checkStateEffects(true);    // as applyGameOnThread does after its own changes
            game.copyLastState();
        }
        return n;
    }

    void applyThenSignal(final Game game, final Wire wire) {
        applyThenSignal(game, wire, false, null);
    }

    /**
     * restore = the table's loadstate (a saved game into a new game); otherwise a dev set-up. after (may be null) runs once the
     * answer is sent: the restore asks for a fresh snapshot with it.
     *
     * A restore also brings Forge's own view of the turn up to date: PhaseHandler.devModeSet sets the turn, the phase and whose
     * turn it is, but the GameView the snapshot reads (gv.getTurn()) is only updated when a turn really begins - LAST1c's live
     * test showed a game restored to turn 5 still saying turn 1 to the table. A dev set-up is left as it was (the soak's
     * mid-game boards and the card check have always seen the set-up's start turn as the old one).
     */
    void applyThenSignal(final Game game, final Wire wire, final boolean restore, final Runnable after) {
        game.getAction().invoke(() -> {
            String failure = null;
            boolean untouched = false;
            try {
                int have = game.getPlayers().size();
                if (restore && playersNamed + 1 != have) {
                    untouched = true;
                    throw new IllegalStateException("the save holds " + (playersNamed + 1) + " player(s), this game " + have);
                }
                applyGameOnThread(game);
                HouseRules.reapply(game);              // round CD3: a set-up (or a LAST1 restore) clears the command zones
                applyMarks(game);
                if (restore) {
                    game.updateTurnForView();
                    game.updatePhaseForView();
                    game.updatePlayerTurnForView();
                    game.updateStackForView();
                    game.updateCombatForView();
                }
            } catch (Throwable e) {
                failure = String.valueOf(e);
                if (!untouched && String.valueOf(e.getMessage()).startsWith("Non-matching number of players")) untouched = true;
                System.err.println("bridge: setup failed: " + e);
            } finally {
                JsonObject m = new JsonObject();
                if (restore && failure != null) {
                    m.addProperty("t", "state_load_failed");
                    m.addProperty("reason", failure);
                    m.addProperty("untouched", untouched);
                } else {
                    m.addProperty("t", "setup_done");
                    m.addProperty("ok", failure == null);
                    if (failure != null) m.addProperty("reason", failure);
                }
                wire.send(m);
                if (after != null) {
                    try {
                        after.run();
                    } catch (RuntimeException e) {
                        System.err.println("bridge: after the set-up: " + e);
                    }
                }
            }
        });
    }
}
