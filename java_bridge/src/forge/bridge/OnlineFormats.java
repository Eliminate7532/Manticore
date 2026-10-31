// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Round MP3a: the formats an online game can be (formats.py on the Python side - keep the two in step; tests/test_mp3a.py
 * checks it). NetHost uses this to refuse a guest's deck of another format, in plain words.
 *
 * A deck's format is read from its .dck text exactly as formats.from_dck reads it: a "Manticore Format=" line first (round
 * CDFIX1: Commander Draft), else "Deck Type=" (Brawl, Constructed = Standard, Limited = Draft), else Commander.
 *
 * Which decks can sit at one table: the same as the deck screen's offline rule (forge_table.start_game, formats.screen) - the
 * decks listed under the same format button. Commander and Commander Draft decks play Commander games together; Standard and
 * Draft decks play Constructed games together; Brawl only with Brawl.
 */
final class OnlineFormats {
    /** key -> the name the deck screen uses. */
    static final Map<String, String> NAMES = new LinkedHashMap<>();
    static {
        NAMES.put("commander", "Commander");
        NAMES.put("brawl", "Brawl");
        NAMES.put("standard", "Standard");
        NAMES.put("commander_draft", "Commander Draft");
        NAMES.put("draft", "Draft");
    }

    private static final Pattern MANTICORE = Pattern.compile("(?im)^\\s*Manticore Format\\s*=\\s*([A-Za-z_]+)\\s*$");
    private static final Pattern DECK_TYPE = Pattern.compile("(?im)^\\s*Deck Type\\s*=\\s*(\\S+)\\s*$");

    private OnlineFormats() {
    }

    static String ofDck(String dck) {
        Matcher m = MANTICORE.matcher(dck == null ? "" : dck);
        if (m.find()) {
            String k = m.group(1).toLowerCase(java.util.Locale.ROOT);
            if (NAMES.containsKey(k)) {
                return k;
            }
        }
        m = DECK_TYPE.matcher(dck == null ? "" : dck);
        if (m.find()) {
            switch (m.group(1).trim().toLowerCase(java.util.Locale.ROOT)) {
                case "brawl": return "brawl";
                case "constructed": return "standard";
                case "limited": return "draft";
                default: return "commander";
            }
        }
        return "commander";
    }

    /** The deck-screen button a format's decks are listed under (formats.screen). */
    static String screen(String key) {
        if ("commander_draft".equals(key)) return "commander";
        if ("draft".equals(key)) return "standard";
        return key;
    }

    /** How Forge plays it: Main's --format (formats.engine). */
    static String engine(String key) {
        String s = screen(key);
        return "standard".equals(s) ? "constructed" : s;
    }

    static boolean hasCommander(String key) {
        return !"constructed".equals(engine(key));
    }

    static boolean compatible(String game, String deck) {
        return screen(game).equals(screen(deck));
    }

    static String name(String key) {
        return NAMES.getOrDefault(key, key);
    }

    /** "This is a Brawl game: choose a Brawl deck (yours is a Commander deck)." */
    static String mismatch(String game, String deck) {
        String want;
        switch (screen(game)) {
            case "commander": want = "a Commander or Commander Draft deck"; break;
            case "standard": want = "a Standard or Draft deck"; break;
            default: want = "a " + name(game) + " deck";
        }
        return "This is a " + name(game) + " game: choose " + want + " (yours is a " + name(deck) + " deck).";
    }
}
