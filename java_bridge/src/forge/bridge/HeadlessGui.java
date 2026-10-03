package forge.bridge;

import forge.GuiDesktop;
import forge.item.PaperCard;
import forge.localinstance.skin.FSkinProp;
import forge.localinstance.skin.ISkinImage;

/**
 * Round 28bb: Forge's desktop GUI services, minus the two that need Forge's skin (its window theme, which the bridge
 * never loads).
 *
 * Found by the soak test (night of 2026-09-27, game 103, the only game the soak seat won): at the end of a game Forge
 * updates the achievements, and for a newly earned one draws a trophy picture from the skin
 * (GuiDesktop.createLayeredImage -> FSkin.getImage). With no skin loaded that throws a NullPointerException ("Can't find
 * an image for FSkinProp IMG_COMMON_TROPHY") on Swing's thread, in the same block that would next call finishGame, so the
 * "game_over" message never reached the table: winning a game froze it. Reproduced live before the fix (the checker's
 * board with the AI's life set to 0). A human winning hit it too.
 *
 * BridgeGui also switches achievements off for the human (see its onGameControllerSet note); this class is the second
 * guard, for any other caller of these two.
 */
public class HeadlessGui extends GuiDesktop {
    @Override
    public ISkinImage createLayeredImage(final PaperCard paperCard, final FSkinProp background, final String overlayFilename, final float opacity) {
        return null;          // nothing here ever shows the picture
    }

    @Override
    public void showImageDialog(final ISkinImage image, final String message, final String title) {
        System.err.printf("[headless] %s: %s (dismissed)%n", title, message);
    }
}
