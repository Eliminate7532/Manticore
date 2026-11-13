package forge.bridge;

import java.util.ArrayList;
import java.util.List;

import org.apache.commons.lang3.tuple.ImmutablePair;

import forge.game.ability.AbilityUtils;
import forge.game.ability.ApiType;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.trigger.WrappedAbility;
import forge.game.zone.MagicStack;

/**
 * bug-1568367922328510465 (Karl Shuh, 6 Nov 2026): "AI played Starving Revenant when it didn't have the life to pay for it and
 * died". Starving Revenant: "When this enters, surveil 2. Then for each card you put on top of your library, you draw a card and
 * you lose 3 life." The AI was at 2 life, kept both cards on top and lost 6 (the report's game log: "Life: AI 1 2 > -4").
 *
 * Forge's AI (PlayerControllerAi.arrangeForSurveil, Forge 9a6f776) decides each card with the scry rule alone; it is not told
 * which spell is surveilling (Player.surveil passes only the cards), so it never sees a cost that depends on how many cards it
 * keeps. This guard looks at what is resolving: when the surveil remembers the cards kept (RememberKept$) and a later part of
 * the same ability makes this player lose life, it works out that loss with Forge's own numbers (the ability's LifeAmount,
 * with the kept cards remembered for a moment, as Player.surveil will remember them) and moves cards from the top to the
 * graveyard until the loss no longer kills the player. It never keeps MORE cards than Forge chose; when nothing like that is
 * resolving, Forge's answer is returned unchanged.
 */
final class SurveilGuard {
    private SurveilGuard() { }

    static ImmutablePair<CardCollection, CardCollection> limit(Player p, ImmutablePair<CardCollection, CardCollection> chosen) {
        try {
            if (chosen == null || chosen.getLeft() == null || chosen.getLeft().isEmpty()) {
                return chosen;
            }
            SpellAbility life = lifeLossAfterKept(p);
            if (life == null || !p.canLoseLife() || p.cantLoseForZeroOrLessLife()) {
                return chosen;
            }
            CardCollection top = new CardCollection(chosen.getLeft());
            CardCollection grave = chosen.getRight() == null ? new CardCollection() : new CardCollection(chosen.getRight());
            int before = top.size();
            while (!top.isEmpty() && loss(life, top) >= p.getLife()) {
                Card c = top.remove(top.size() - 1);
                grave.add(c);
            }
            if (top.size() == before) {
                return chosen;
            }
            System.err.println("bridge: surveil guard: " + p.getName() + " keeps " + top.size() + " of " + before
                    + " on top (" + life.getHostCard().getName() + ": life " + p.getLife() + ")");
            return ImmutablePair.of(top, grave);
        } catch (RuntimeException e) {
            return chosen;                       // Forge's own answer, as before
        }
    }

    /** The LoseLife part, for this player, that follows a RememberKept surveil in the ability resolving now; else null. */
    static SpellAbility lifeLossAfterKept(Player p) {
        MagicStack stack = p.getGame().getStack();
        if (stack == null || !stack.isResolving() || stack.isEmpty()) {
            return null;
        }
        SpellAbility sa = stack.peekAbility();
        if (sa instanceof WrappedAbility) {
            sa = ((WrappedAbility) sa).getWrappedAbility();
        }
        boolean kept = false;
        for (SpellAbility s = sa; s != null; s = s.getSubAbility()) {
            if (s.getApi() == ApiType.Surveil && s.hasParam("RememberKept")) {
                kept = true;
            } else if (kept && s.getApi() == ApiType.LoseLife && s.hasParam("LifeAmount")) {
                String d = s.hasParam("Defined") ? s.getParam("Defined") : "You";
                if ("You".equals(d) && s.getActivatingPlayer() == p) {
                    return s;
                }
            }
        }
        return null;
    }

    /** Forge's own life loss if these cards are kept: they are remembered for the calculation, then forgotten again. */
    static int loss(SpellAbility life, CardCollection kept) {
        Card host = life.getHostCard();
        List<Card> added = new ArrayList<>();
        for (Card c : kept) {
            if (!host.isRemembered(c)) {
                added.add(c);
            }
        }
        host.addRemembered(added);
        try {
            return AbilityUtils.calculateAmount(host, life.getParam("LifeAmount"), life);
        } finally {
            host.removeRemembered(added);
        }
    }
}
