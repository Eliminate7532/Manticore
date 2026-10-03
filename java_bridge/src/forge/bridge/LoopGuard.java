package forge.bridge;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonObject;

import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.SpellAbility;

/**
 * Round 28e: stops Forge's AI from repeating one ability forever.
 *
 * Soak nights 7 and 9: the Kinnan AI had Kinnan, Bonder Prodigy, Grim Monolith and a Copy Artifact copying it. Each Monolith
 * taps for {C}{C}{C}, Kinnan adds one more, and 4 is exactly the other Monolith's untap cost, so the AI untapped one with the
 * other, back and forth, 6,530 and 7,134 times, gaining nothing, until the soak's 30-minute limit ended the game. Forge's AI
 * judges each activation on its own and never sees that it is going round in circles. For a person at the table it is a
 * frozen game.
 *
 * The guard: an AI player may use the same ability of the same card at most {@link #cap} times in one turn. After that,
 * when the AI picks that ability again, it passes priority instead (Forge's PhaseHandler treats a null choice as "I pass"),
 * and the bridge says so once per card and turn ({"t":"ai_loop_guard"}). Other abilities, and the same ability next turn,
 * are not affected. Playing a land is never counted. The default of 10 is well above anything the AI does on purpose (the
 * most Basalt Monolith untaps in one turn in 64 evaluation games was 4) and low because every repeat goes on the stack, so a
 * person at the table may have to pass priority for each one. A scripted combo line (BRIEF_AI_COMBO_LINES) would have to be
 * exempted from it. Checked live (round 28e): Kinnan + two Grim Monoliths, one tapped, in the other player's end step -
 * guard off: 491 untaps in 60 seconds and the turn never ended; guard on: it stops at the limit and the game moves on.
 */
final class LoopGuard {
    static final int DEFAULT_CAP = 10;
    static int cap = DEFAULT_CAP;                  // --loop-cap N on the command line; 0 turns the guard off

    private LoopGuard() { }

    /** The AI opponent as Forge's GamePlayerUtil.createAiPlayer made it, with the same name, profile and avatar, but guarded. */
    static LobbyPlayer guarded(LobbyPlayer made) {
        if (!(made instanceof LobbyPlayerAi) || cap <= 0) {
            return made;
        }
        LobbyPlayerAi src = (LobbyPlayerAi) made;
        Lobby g = new Lobby(src.getName());
        g.setAiProfile(src.getAiProfile());
        g.setAvatarIndex(src.getAvatarIndex());
        g.setSleeveIndex(src.getSleeveIndex());
        return g;
    }

    /** LobbyPlayerAi whose in-game controller is {@link Controller}. Forge's own createControllerFor is private, so it is
     *  repeated here: a PlayerControllerAi with no simulation option (createAiPlayer passes no options either). */
    static final class Lobby extends LobbyPlayerAi {
        Lobby(String name) {
            super(name, null);
        }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player ai = new Player(getName(), game, id);
            ai.setFirstController(new Controller(game, ai, this));
            return ai;
        }

        @Override
        public PlayerController createMindSlaveController(Player master, Player slave) {
            return new Controller(slave.getGame(), slave, this);
        }
    }

    static final class Controller extends PlayerControllerAi {
        private int turn = -1;
        private final Map<String, Integer> uses = new HashMap<>();
        private final Set<String> told = new HashSet<>();

        Controller(Game game, Player p, LobbyPlayer lp) {
            super(game, p, lp);
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            List<SpellAbility> chosen = super.chooseSpellAbilityToPlay();
            if (chosen == null || chosen.isEmpty() || cap <= 0) {
                return chosen;
            }
            int now = getGame().getPhaseHandler().getTurn();
            if (now != turn) {
                turn = now;
                uses.clear();
                told.clear();
            }
            for (SpellAbility sa : chosen) {
                if (sa == null || sa.isLandAbility()) {
                    continue;
                }
                String k = key(sa);
                if (uses.getOrDefault(k, 0) >= cap) {
                    tell(sa, k);
                    return null;                       // pass priority instead
                }
            }
            for (SpellAbility sa : chosen) {
                if (sa != null && !sa.isLandAbility()) {
                    uses.merge(key(sa), 1, Integer::sum);
                }
            }
            return chosen;
        }

        int uses(SpellAbility sa) {
            return uses.getOrDefault(key(sa), 0);
        }

        private static String key(SpellAbility sa) {
            Card host = sa.getHostCard();
            return (host == null ? -1 : host.getId()) + "|" + sa.getDescription();
        }

        private void tell(SpellAbility sa, String k) {
            if (!told.add(k)) {
                return;
            }
            Card host = sa.getHostCard();
            String card = host == null ? "?" : host.getName();
            System.err.println("bridge: loop guard: " + getPlayer().getName() + " used " + card + " (" + sa.getDescription()
                    + ") " + cap + " times this turn; passing instead");
            if (Checks.wire != null) {
                JsonObject m = new JsonObject();
                m.addProperty("t", "ai_loop_guard");
                m.addProperty("player", getPlayer().getName());
                m.addProperty("card", card);
                m.addProperty("cardId", host == null ? -1 : host.getId());
                m.addProperty("ability", sa.getDescription());
                m.addProperty("turn", turn);
                m.addProperty("count", cap);
                Checks.wire.send(m);
            }
        }
    }
}
