// SPDX-License-Identifier: GPL-3.0-or-later
package forge.rpgtools;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.ObjectInputStream;
import java.io.OutputStreamWriter;
import java.io.PrintStream;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;

import org.apache.commons.lang3.tuple.Pair;

import forge.GuiDesktop;
import forge.StaticData;
import forge.card.CardEdition;
import forge.card.CardRarity;
import forge.card.CardRules;
import forge.deck.CardArchetypeLDAGenerator;
import forge.deck.Deck;
import forge.deck.DeckSection;
import forge.deck.DeckgenUtil;
import forge.deck.io.Archetype;
import forge.game.GameFormat;
import forge.gui.GuiBase;
import forge.item.PaperCard;
import forge.item.SealedTemplate;
import forge.item.generation.BoosterGenerator;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.MyRandom;

/**
 * RPG plan, phase 2 (Round RPG2): a developer tool that reads Forge's own data and writes JSON for the RPG mode.
 * It is NOT part of the game bridge (java_bridge/): it lives in its own jar (java_rpg/rpg_tools.jar), so building it never
 * changes the bridge's source stamp, and saved games stay resumable. Run with forge_runtime/ as the working folder:
 *
 *   java -cp forge.jar:rpg_tools.jar forge.rpgtools.RpgData archetypes OUT.json Vintage Legacy ...
 *   java ... RpgData boosters OUT.json SEED COUNT SET [SET ...]
 *   java ... RpgData decks OUT.json SEED FORMAT COUNT [ARCHETYPE-NAME ...]   (no names: every archetype Forge keeps)
 *   java ... RpgData catalogue OUT.json
 *
 * "archetypes" only deserialises res/deckgendecks/<Format>.raw.dat (Forge's CardThemedLDAIO format: a Java-serialised
 * List<forge.deck.io.Archetype>); the others start Forge's model (FModel.initialize, about 10 s) and use Forge's own
 * BoosterGenerator and DeckgenUtil.buildLDACArchetypeDeck. Every random choice goes through Forge's MyRandom, seeded here,
 * so the same SEED gives the same packs and decks for the same Forge build.
 */
public final class RpgData {

    private RpgData() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            usage();
            return;
        }
        String cmd = args[0];
        String out = args[1];
        try (Writer w = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
            switch (cmd) {
                case "archetypes" -> archetypes(w, rest(args, 2));
                case "boosters" -> boosters(w, Long.parseLong(args[2]), Integer.parseInt(args[3]), rest(args, 4));
                case "decks" -> decks(w, Long.parseLong(args[2]), args[3], Integer.parseInt(args[4]), rest(args, 5));
                case "catalogue" -> catalogue(w);
                default -> usage();
            }
        }
        System.exit(0);                  // Forge's model leaves non-daemon threads behind
    }

    static void usage() {
        System.err.println("usage: RpgData archetypes OUT FORMAT... | boosters OUT SEED COUNT SET... | decks OUT SEED FORMAT COUNT [NAME...]"
                + " | catalogue OUT");
    }

    static List<String> rest(String[] args, int from) {
        List<String> r = new ArrayList<>();
        for (int i = from; i < args.length; i++) {
            r.add(args[i]);
        }
        return r;
    }

    // ---- archetypes: the raw LDA data, no Forge model needed ---------------------------------------------------------------

    @SuppressWarnings("unchecked")
    static void archetypes(Writer w, List<String> formats) throws Exception {
        w.write("{\"formats\": {");
        boolean firstF = true;
        for (String f : formats) {
            List<Archetype> list;
            try (ObjectInputStream s = new ObjectInputStream(new FileInputStream("res/deckgendecks/" + f + ".raw.dat"))) {
                list = (List<Archetype>) s.readObject();
            }
            w.write((firstF ? "" : ",") + "\n " + q(f) + ": [");
            firstF = false;
            boolean firstA = true;
            for (Archetype a : list) {
                w.write((firstA ? "" : ",") + "\n  {\"name\": " + q(a.getName()) + ", \"decks\": " + a.getDeckCount() + ", \"cards\": [");
                firstA = false;
                boolean firstC = true;
                for (Pair<String, Double> p : a.getCardProbabilities()) {
                    w.write((firstC ? "" : ", ") + "[" + q(p.getLeft()) + ", " + p.getRight() + "]");
                    firstC = false;
                }
                w.write("]}");
            }
            w.write("]");
        }
        w.write("\n}}\n");
    }

    // ---- Forge's model ----------------------------------------------------------------------------------------------------

    static void initForge(long seed) {
        System.setProperty("java.awt.headless", "true");
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, null);
        FModel.getPreferences().setPref(FPref.UI_ENABLE_SOUNDS, false);
        FModel.getPreferences().setPref(FPref.UI_ENABLE_MUSIC, false);
        MyRandom.setRandom(new Random(seed));
    }

    static String card(PaperCard c) {
        return "[" + q(c.getName()) + ", " + q(c.getEdition()) + ", " + q(c.getCollectorNumber()) + ", " + q(String.valueOf(c.getRarity()))
                + (c.isFoil() ? ", true" : "") + "]";
    }

    // ---- boosters: Forge's own booster generator --------------------------------------------------------------------------

    static void boosters(Writer w, long seed, int count, List<String> sets) throws Exception {
        initForge(seed);
        w.write("{\"seed\": " + seed + ", \"sets\": {");
        boolean firstS = true;
        for (String code : sets) {
            w.write((firstS ? "" : ",") + "\n " + q(code) + ": ");
            firstS = false;
            CardEdition ed = StaticData.instance().getEditions().get(code);
            if (ed == null || !ed.hasBoosterTemplate()) {
                w.write("{\"error\": " + q(ed == null ? "unknown set" : "no booster") + "}");
                continue;
            }
            SealedTemplate tpl = ed.getBoosterTemplate();
            w.write("{\"name\": " + q(ed.getName()) + ", \"template\": " + q(String.valueOf(tpl)) + ", \"packs\": [");
            for (int i = 0; i < count; i++) {
                List<PaperCard> pack = BoosterGenerator.getBoosterPack(tpl);
                w.write((i == 0 ? "" : ",") + "\n  [");
                boolean firstC = true;
                for (PaperCard c : pack) {
                    w.write((firstC ? "" : ", ") + card(c));
                    firstC = false;
                }
                w.write("]");
            }
            w.write("]}");
        }
        w.write("\n}}\n");
    }

    // ---- decks: Forge's archetype deck builder -----------------------------------------------------------------------------

    static void decks(Writer w, long seed, String formatName, int count, List<String> names) throws Exception {
        initForge(seed);
        GameFormat format = FModel.getFormats().getFormat(formatName);
        if (format == null) {
            throw new IllegalArgumentException("no such format: " + formatName);
        }
        if (!CardArchetypeLDAGenerator.initializeFormat(format.getName())) {
            throw new IllegalStateException("Forge could not read its archetype data for " + formatName);
        }
        List<Archetype> all = CardArchetypeLDAGenerator.ldaArchetypes.get(format.getName());
        List<Archetype> wanted = new ArrayList<>();
        for (Archetype a : all) {
            if (names.isEmpty() || names.contains(a.getName())) {
                wanted.add(a);
            }
        }
        w.write("{\"seed\": " + seed + ", \"format\": " + q(format.getName()) + ", \"decks\": [");
        boolean first = true;
        PrintStream realOut = System.out;
        for (Archetype a : wanted) {
            int index = all.indexOf(a);                  // Forge's data can hold two archetypes of one name
            for (int i = 0; i < count; i++) {
                String error = null;
                Deck d = null;
                System.setOut(System.err);       // Forge prints its "Wrong card count" notes on stdout
                try {
                    d = DeckgenUtil.buildLDACArchetypeDeck(a, format, true);
                } catch (RuntimeException e) {
                    error = e.toString();
                } finally {
                    System.setOut(realOut);
                }
                w.write((first ? "" : ",") + "\n {\"archetype\": " + q(a.getName()) + ", \"index\": " + index + ", \"metaDecks\": "
                        + a.getDeckCount());
                first = false;
                if (d == null) {
                    w.write(", \"error\": " + q(error) + "}");
                    continue;
                }
                // Forge rebuilds with a RANDOM archetype when a build comes out wrong (card count, too many lands): its
                // deck name says which archetype it really is ("<archetype> Generated Deck").
                w.write(", \"deckName\": " + q(d.getName()) + ", \"main\": " + pool(d.getMain()) + ", \"sideboard\": "
                        + (d.has(DeckSection.Sideboard) ? pool(d.get(DeckSection.Sideboard)) : "[]") + "}");
            }
        }
        w.write("\n]}\n");
    }

    /** [[name, count], ...] - printings of one card (a deck's Mountains from several sets) merged under its name, first seen first. */
    static String pool(forge.deck.CardPool p) {
        Map<String, Integer> byName = new java.util.LinkedHashMap<>();
        for (Map.Entry<PaperCard, Integer> e : p) {
            byName.merge(e.getKey().getName(), e.getValue(), Integer::sum);
        }
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Map.Entry<String, Integer> e : byName.entrySet()) {
            sb.append(first ? "" : ", ").append("[").append(q(e.getKey())).append(", ").append(e.getValue()).append("]");
            first = false;
        }
        return sb.append("]").toString();
    }

    // ---- catalogue: every card Forge can play, once, with what the RPG needs to sort and price it ----------------------------

    static void catalogue(Writer w) throws Exception {
        initForge(1);
        Map<String, List<PaperCard>> byName = new TreeMap<>();
        for (PaperCard c : StaticData.instance().getCommonCards().getAllCards()) {
            byName.computeIfAbsent(c.getName(), k -> new ArrayList<>()).add(c);
        }
        w.write("{\"cards\": [");
        boolean first = true;
        for (Map.Entry<String, List<PaperCard>> e : byName.entrySet()) {
            CardRules r = e.getValue().get(0).getRules();
            CardRarity lowest = null;
            TreeMap<String, String> sets = new TreeMap<>();
            for (PaperCard c : e.getValue()) {
                CardRarity cr = c.getRarity();
                if (rank(cr) >= 0 && (lowest == null || rank(cr) < rank(lowest))) {
                    lowest = cr;
                }
                sets.put(c.getEdition(), String.valueOf(cr));
            }
            w.write((first ? "" : ",") + "\n [" + q(e.getKey()) + ", " + q(r.getType().toString()) + ", "
                    + (int) Math.round(r.getManaCost().getCMC()) + ", " + q(r.getColor().toString()) + ", " + q(String.valueOf(lowest))
                    + ", " + copyLimit(r.getOracleText()) + ", [");
            first = false;
            boolean firstS = true;
            for (Map.Entry<String, String> s : sets.entrySet()) {
                w.write((firstS ? "" : ", ") + "[" + q(s.getKey()) + ", " + q(s.getValue()) + "]");
                firstS = false;
            }
            w.write("]]");
        }
        w.write("\n],\n\"editions\": [");
        first = true;
        for (CardEdition ed : StaticData.instance().getEditions()) {
            w.write((first ? "" : ",") + "\n [" + q(ed.getCode()) + ", " + q(ed.getName()) + ", "
                    + q(ed.getDate() == null ? "" : new java.text.SimpleDateFormat("yyyy-MM-dd").format(ed.getDate())) + ", "
                    + q(String.valueOf(ed.getType())) + ", " + ed.hasBoosterTemplate() + "]");
            first = false;
        }
        w.write("\n]}\n");
    }

    static final String[] NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen"};

    /** 0 = the usual limit; -1 = "A deck can have any number of cards named ..."; N = "A deck can have up to N cards named ...". */
    static int copyLimit(String oracle) {
        if (oracle == null) {
            return 0;
        }
        String o = oracle.toLowerCase(java.util.Locale.ROOT);
        if (o.contains("a deck can have any number of cards named")) {
            return -1;
        }
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("a deck can have up to (\\w+) cards named").matcher(o);
        if (m.find()) {
            String word = m.group(1);
            for (int i = 0; i < NUMBER_WORDS.length; i++) {
                if (NUMBER_WORDS[i].equals(word)) {
                    return i;
                }
            }
            try {
                return Integer.parseInt(word);
            } catch (NumberFormatException e) {
                return 0;
            }
        }
        return 0;
    }

    static int rank(CardRarity r) {
        if (r == null) {
            return -1;
        }
        return switch (r) {
            case BasicLand -> 0;
            case Common -> 1;
            case Uncommon -> 2;
            case Rare -> 3;
            case MythicRare -> 4;
            case Special -> 5;
            default -> -1;
        };
    }

    // ---- JSON ---------------------------------------------------------------------------------------------------------------

    static String q(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        return sb.append('"').toString();
    }
}
