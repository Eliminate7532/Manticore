// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.deck.Deck;
import forge.deck.io.DeckSerializer;
import forge.game.GameType;
import forge.game.player.RegisteredPlayer;
import forge.gamemodes.match.HostedMatch;
import forge.gui.interfaces.IGuiGame;
import forge.interfaces.IGameController;
import forge.player.LobbyPlayerHuman;
import forge.player.GamePlayerUtil;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLServerSocket;
import javax.net.ssl.SSLSocket;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.PrintStream;
import java.io.Writer;
import java.net.BindException;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Round MP1: a 1v1 Commander game between two people, played over the internet, with one Forge engine on the host's PC.
 *
 * Seat 1 is the host's own table, over stdin/stdout exactly like Main. Seat 2 is the guest's table, on a TLS 1.3 connection
 * that speaks the same JSON lines (protocol 2) after a short handshake. Each seat has its own BridgeGui, so each gets its own
 * snapshots, built for its own player: the opponent's hand and library are never sent, only counts (Snapshot.card uses Forge's
 * canBeShownTo). Based on the 2026-09-25 spike (docs/incoming/multiplayer_1v1/); the design is claude/SONNET_SPEC_MP1_2026-09-25.md.
 *
 *   java -cp bridge.jar:forge.jar forge.bridge.NetHost --deck my.dck --name Karl --port 36800 --keystore host.p12
 *        --guest-deck guest.dck [--bind 0.0.0.0] [--upnp] [--seed N] [--code abcd1234] [--parent-pid N] [--dev]
 *   environment: MANTICORE_NET_PASSWORD (the game password), MANTICORE_KEYSTORE_PASS (the keystore's own password).
 *   Both go in the environment, not on the command line, where any program on the PC could read them.
 *
 * The handshake (one JSON line each way, inside TLS):
 *   guest: {"c":"hello","v":2,"name":"Sam","password":"...","code":"<version code>","deck":{"name":"...","dck":"<.dck text>"}}
 *   host:  {"t":"welcome","v":2,"host":"Karl","seat":2,"code":"<version code>","token":"<rejoin secret>","grace":60}
 *       or {"t":"refused","reason":"password"|"full"|"deck"|"version","text":"..."} and the connection closes.
 *   Five wrong passwords from one address and that address is ignored for a minute (--block-seconds for tests).
 *
 * Round MP2 (protocol v2):
 *   - Reconnecting. A guest whose connection drops keeps its seat for --grace-seconds (60): Forge's questions to it wait, the
 *     host's table gets {"t":"peer_dropped","who":"guest","grace"}, and the guest's table connects again with
 *     {"c":"rejoin","v":2,"token":"..."} (the token from its welcome; wrong tokens count as wrong passwords). It is welcomed
 *     with "rejoined":true, gets a fresh snapshot and every question still waiting (SeatWire, Wire.resendPending), and the
 *     host's table gets {"t":"peer_back"}. After the grace period the seat is given up and the game ends as in MP1.
 *     A guest that means to go sends {"c":"leave"} first, so the game ends at once instead of after the grace period.
 *   - Staying in touch. Every --heartbeat-seconds (5) each remote table gets {"t":"hb"} and answers {"c":"hb"}; a connection
 *     that has said nothing for --silence-seconds (20) is dropped, so a dead link (Wi-Fi gone, no FIN) is noticed quickly.
 *   - Spectators. {"c":"watch","v":2,"name","password"} once the game is running: up to 4 people get a snapshot in which every
 *     hand and library card is face down ("spectator":true; BridgeGui.spectator, Snapshot.spectatorView) and may send nothing
 *     but flush, hb and leave. Both players' tables get {"t":"spectator_joined"/"spectator_left","name"}.
 *
 * To the host's table, besides the normal stream: {"t":"hosting","port","bind"} (ready for the guest), {"t":"upnp",...}
 * (see Upnp), {"t":"join_refused","reason"} (someone tried and was turned away), {"t":"guest_joined","name","code"},
 * {"t":"peer_left","who":"guest","why"}, {"t":"hosting_cancelled"}, {"t":"host_failed","reason","text"} (then exits 1).
 * The host's table may send {"c":"cancel_host"} while waiting. To the guest: {"t":"peer_left","who":"host"} when the host's
 * table closes, and {"t":"refused_cmd","c"} for a command a guest may not send.
 */
public class NetHost {
    // Round MP3a: 3 - the welcome and the invite carry the game's format and house rules, and the guest's deck must be of that
    // format. A copy speaking 2 is refused with "version" both ways (a v2 guest would otherwise join a Brawl game with a
    // Commander deck unchecked, and a v2 host can only play Commander).
    static final int PROTOCOL_V = 3;                        // round MP2: rejoin, watch, hb, leave
    static final int MAX_SPECTATORS = 4;
    static final int HELLO_MAX = 256 * 1024;                // the hello carries the guest's whole deck
    static final int DECK_MAX = 200 * 1024;
    static final int REMOTE_LINE_MAX = 64 * 1024;           // no real command comes near it
    static final int REMOTE_RATE = 200;                     // lines a second from the guest
    static final int HELLO_SECONDS = 15;
    static final int WRONG_TRIES = 5;
    static final int NAME_MAX = 24;
    /** Commands a guest may send; everything else (quit, setup, anything unknown) is refused and answered with refused_cmd. */
    static final Set<String> REMOTE_ALLOWED = Set.of("ok", "cancel", "card", "player", "mana", "undo", "alpha", "concede", "flush",
            "stops", "yield", "autoyield", "trigger", "autopass", "yieldreset",
            "hold", "passturn", "fullcontrol", "alwaysstop",                                   // patch 43
            "speed",                                                                           // round PRI1
            "pauses");                                                                         // round PERF2

    static int blockSeconds = 60;
    static int graceSeconds = 60;                           // round MP2: how long a dropped guest's seat is held
    static int heartbeatSeconds = 5;
    static int silenceSeconds = 20;
    static int dropNoticeDelayMs = 0;                       // tests (round MP2e): a pause between a drop and its announcement
    static String hostName = "Host";
    static String code = "";
    static String guestDeckPath;
    /** Round MP3a: the game's format as the deck screen names it (commander, brawl, standard, commander_draft, draft); Main.format
     *  holds how Forge plays it (commander, brawl, constructed). Set by --game-format and --format. */
    static String gameFormat = "commander";
    static Deck hostDeck;
    static String password;
    static Wire hostWire;
    static volatile BridgeGui hostGui;
    static int guestsWanted = 1;                            // round MP2c: --guests N (1-3), the people who join over the network
    // Round CD5: --draft - drafting together before the game. The lobby takes up to --guests friends without decks; the host's
    // table runs the draft (Python's draft_pod) and NetHost only carries its messages: {"c":"draft_send","seat","msg"} goes to that
    // guest as {"t":"draft","msg"}, and a guest's {"c":"draft","msg"} reaches the host's table as {"t":"draft_from","seat","name",
    // "msg"}. {"c":"draft_lock"} closes the lobby (the host pressed Start): from then on a dropped friend keeps the seat for as
    // long as the draft lasts (everyone waits - Karl, 27 Oct), and can come back with the rejoin token or, with the password,
    // by name. {"c":"draft_game"} gives every seat its deck and starts the game in this process, as MP2c's lobby does.
    static boolean draftMode = false;
    static volatile boolean draftLocked = false;
    static int draftReconnectSeconds = 900;                 // the welcome's "grace" in a draft: how long a guest's table retries
    static final List<String> aiDeckPaths = new ArrayList<>();      // round MP2c: --opponent FILE, one per AI seat
    static final List<Deck> aiDecks = new ArrayList<>();
    /** The guests in seat order, from the moment each joins (round MP2: a SeatWire outlives its connection). */
    static final List<SeatWire> guests = new java.util.concurrent.CopyOnWriteArrayList<>();
    static final List<SeatWire> watchers = new java.util.concurrent.CopyOnWriteArrayList<>();
    static volatile String guestName;                       // the first guest's (MP1's one guest)
    static volatile boolean started = false;                // every guest has joined and the game is running
    // Round MP2b: saving and resuming an online game
    static String journalPath;                              // --journal FILE: every command applied, {"s":seat,"c":{...}}
    static String replayPath;                               // --replay FILE: an earlier journal to play back first
    static final List<String> resumeGuests = new ArrayList<>();      // --resume-guest "seat|name|deck file", in seat order
    static volatile boolean replayDone = true;              // false while ResumeFeeder plays the saved game back
    static Writer journalOut;
    static final Object journalLock = new Object();
    static final AtomicBoolean heartbeatOn = new AtomicBoolean(false);
    static volatile boolean gameOver = false;
    static final AtomicBoolean leaving = new AtomicBoolean(false);
    static final Map<String, int[]> wrong = new ConcurrentHashMap<>();         // address -> {wrong tries}
    static final Map<String, Long> blockedUntil = new ConcurrentHashMap<>();
    static final ScheduledExecutorService timers = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "nethost-timer");
        t.setDaemon(true);
        return t;
    });

    public static void main(String[] args) throws Exception {
        PrintStream proto = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        System.setOut(System.err);                          // Forge chatters on System.out; keep the wire clean
        System.setProperty("java.awt.headless", "true");
        String deckPath = null, keystore = null, bind = "0.0.0.0";
        int port = 36800;
        Long seed = null, parentPid = null;
        boolean upnp = false;
        String badArg = null;                               // round MP3a
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--deck": deckPath = args[++i]; break;
                case "--name": hostName = args[++i]; break;
                case "--port": port = Integer.parseInt(args[++i]); break;
                case "--bind": bind = args[++i]; break;
                case "--keystore": keystore = args[++i]; break;
                case "--guest-deck": guestDeckPath = args[++i]; break;
                case "--code": code = args[++i]; break;
                case "--seed": seed = Long.parseLong(args[++i]); break;
                case "--upnp": upnp = true; break;
                case "--dev": Main.dev = true; break;
                case "--ai-timeout": Main.aiTimeout = Integer.parseInt(args[++i]); break;
                case "--forge-undo": {                                 // round FB2, as in Main
                    String v = args[++i];
                    if (!Main.FORGE_UNDO.containsKey(v)) throw new IllegalArgumentException("--forge-undo must be off, single or free");
                    Main.forgeUndo = v;
                    break;
                }
                case "--threat-ai": Threats.on = !"off".equalsIgnoreCase(args[++i]); break;     // round AI2
                case "--plan-cache": {                                                             // round AIT2
                    String m = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (PlanCache.MODES.contains(m)) PlanCache.mode = m;
                    break;
                }
                case "--attack-budget": AttackCache.budgetSeconds = Integer.parseInt(args[++i]); break;   // round AI-ATTACK
                case "--attack-cache": {                                                     // round AI-ATTACK, see AttackCache (off = Forge's own)
                    String m = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (AttackCache.MODES.contains(m)) AttackCache.mode = m;
                    break;
                }
                case "--parent-pid": parentPid = Long.parseLong(args[++i]); break;
                case "--block-seconds": blockSeconds = Integer.parseInt(args[++i]); break;           // tests
                case "--stall-seconds": RemoteWire.stallSeconds = Integer.parseInt(args[++i]); break; // tests
                case "--grace-seconds": graceSeconds = Integer.parseInt(args[++i]); break;           // round MP2
                case "--heartbeat-seconds": heartbeatSeconds = Integer.parseInt(args[++i]); break;   // tests
                case "--silence-seconds": silenceSeconds = Integer.parseInt(args[++i]); break;       // tests
                case "--drop-notice-delay-ms": dropNoticeDelayMs = Integer.parseInt(args[++i]); break; // tests (round MP2e)
                case "--guests": guestsWanted = Integer.parseInt(args[++i]); break;                 // round MP2c
                case "--draft": draftMode = true; break;                                             // round CD5
                case "--draft-reconnect-seconds": draftReconnectSeconds = Integer.parseInt(args[++i]); break;   // round CD5
                case "--opponent": aiDeckPaths.add(args[++i]); break;                               // round MP2c: an AI seat
                case "--journal": journalPath = args[++i]; break;                                   // round MP2b
                case "--replay": replayPath = args[++i]; break;                                     // round MP2b
                case "--resume-guest": resumeGuests.add(args[++i]); break;                          // round MP2b
                case "--format": {                                                                  // round MP3a, as Main
                    String f = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (f.equals("commander") || f.equals("brawl") || f.equals("constructed")) Main.format = f;
                    else badArg = "unknown format " + args[i];
                    break;
                }
                case "--game-format": {                                                             // round MP3a
                    String f = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (OnlineFormats.NAMES.containsKey(f)) gameFormat = f;
                    else badArg = "unknown game format " + args[i];
                    break;
                }
                case "--house-rule": {                                                              // round MP3a, as Main
                    String r = args[++i].toLowerCase(java.util.Locale.ROOT);
                    if (HouseRules.KNOWN.contains(r)) HouseRules.active.add(r);
                    else badArg = "unknown house rule " + args[i];
                    break;
                }
                default: System.err.println("nethost: unknown argument " + args[i]);
            }
        }
        if (parentPid != null) {
            ParentWatch.start(parentPid);                    // round 29a: a killed program never leaves java.exe behind
        }
        Main.online = true;
        hostWire = new Wire(proto);
        Checks.wire = hostWire;
        password = System.getenv("MANTICORE_NET_PASSWORD");
        String storePass = System.getenv("MANTICORE_KEYSTORE_PASS");
        if (deckPath == null || keystore == null || guestDeckPath == null || password == null || password.isEmpty()
                || storePass == null) {
            hostFailed("args", "NetHost needs --deck, --keystore, --guest-deck, MANTICORE_NET_PASSWORD and MANTICORE_KEYSTORE_PASS");
            return;
        }
        if (badArg != null) {
            hostFailed("args", badArg);
            return;
        }
        if (!OnlineFormats.engine(gameFormat).equals(Main.format)) {   // round MP3a: --format must be how --game-format is played
            hostFailed("args", "the game format " + gameFormat + " is played as " + OnlineFormats.engine(gameFormat)
                    + ", not " + Main.format);
            return;
        }
        if (!resumeGuests.isEmpty()) {
            guestsWanted = resumeGuests.size();                  // round MP2b: the saved game's guests
        }
        if (guestsWanted < 1 || guestsWanted > 3 || totalPlayers() > 4) {
            hostFailed("args", "An online game has 1-3 guests and at most 4 players (asked: " + guestsWanted + " guest(s), "
                    + aiDeckPaths.size() + " AI).");
            return;
        }

        // The port first: a port that's in use is the commonest problem, and it's worth saying at once, before Forge's 10-20 s.
        SSLServerSocket server;
        try {
            server = openServer(keystore, storePass.toCharArray(), bind, port);
        } catch (BindException e) {
            hostFailed("port", "Port " + port + " is already in use on this PC (" + e.getMessage() + "). Close the other program, "
                    + "or choose another port.");
            return;
        } catch (Exception e) {
            hostFailed("tls", "Could not set up the encrypted connection: " + e);
            return;
        }
        if (upnp) {
            Upnp.start(port, hostWire::send);                // runs beside Forge's start-up; reports {"t":"upnp"} when it knows
        }
        // The host's own table: before the game only cancel_host and quit mean anything; once the game runs, everything goes to
        // Main.handle for seat 1, as in a normal game.
        Thread hostReader = new Thread(() -> {
            hostWire.readLoop(NetHost::hostCommand);
            hostLeft();
        }, "host-seat");
        hostReader.setDaemon(true);
        hostReader.start();

        Main.initForge(hostName, seed);
        hostDeck = draftMode ? null : DeckSerializer.fromFile(new File(deckPath));     // round CD5: a draft's deck comes later
        if (hostDeck == null && !draftMode) {
            hostFailed("deck", "could not read deck " + deckPath);
            return;
        }
        for (String path : aiDeckPaths) {                         // round MP2c: the AI seats' decks, read before anyone joins
            Deck d = DeckSerializer.fromFile(new File(path));
            if (d == null) {
                hostFailed("deck", "could not read the AI deck " + path);
                return;
            }
            aiDecks.add(d);
        }
        JsonObject hosting = new JsonObject();
        hosting.addProperty("t", "hosting");
        hosting.addProperty("port", port);
        hosting.addProperty("bind", bind);
        hostWire.send(hosting);
        if (!resumeGuests.isEmpty()) {
            try {
                startResumed();
            } catch (Exception e) {
                hostFailed("resume", "Could not restore the saved game: " + e);
                return;
            }
        }
        acceptLoop(server);
    }

    // ---- round MP2b: the journal, and playing a saved game back -----------------------------------------------------------
    /** One applied command into the journal (if there is one). Never the password or a token: commands carry neither. */
    static void journal(int seat, JsonObject cmd) {
        if (journalPath == null) {
            return;
        }
        synchronized (journalLock) {
            try {
                if (journalOut == null) {
                    File f = new File(journalPath);
                    File dir = f.getAbsoluteFile().getParentFile();
                    if (dir != null) {
                        dir.mkdirs();
                    }
                    journalOut = new OutputStreamWriter(new FileOutputStream(f, false), StandardCharsets.UTF_8);
                }
                JsonObject line = new JsonObject();
                line.addProperty("s", seat);
                line.add("c", cmd);
                journalOut.write(line + "\n");
                journalOut.flush();
            } catch (IOException e) {
                System.err.println("nethost: the game journal could not be written: " + e);
                journalPath = null;
            }
        }
    }

    static BridgeGui guiForSeat(int seat) {
        if (seat == 1) {
            return hostGui;
        }
        for (SeatWire g : guests) {
            if (g.seat == seat) {
                return g.gui;
            }
        }
        return null;
    }

    static Wire wireForSeat(int seat) {
        if (seat == 1) {
            return hostWire;
        }
        for (SeatWire g : guests) {
            if (g.seat == seat) {
                return g;
            }
        }
        return null;
    }

    /** The saved game's seats are made up front (no connections yet), the game starts at once, and ResumeFeeder plays the saved
     *  commands back on its own thread. Guests coming back meanwhile wait in the lobby (resumeJoin). */
    static void startResumed() throws Exception {
        for (String spec : resumeGuests) {
            String[] parts = spec.split("\\|", 3);
            SeatWire seat = new SeatWire(Integer.parseInt(parts[0]), parts[1], newToken(), REMOTE_RATE);
            seat.deckPath = parts[2];
            guests.add(seat);
        }
        List<ResumeFeeder.Entry> entries = replayPath == null ? new ArrayList<>() : ResumeFeeder.read(replayPath);
        replayDone = entries.isEmpty();
        started = true;
        startGame();
        if (entries.isEmpty()) {
            replayFinished(new ResumeFeeder(entries));
            return;
        }
        System.err.println("nethost: playing back " + entries.size() + " saved actions");
        ResumeFeeder feeder = new ResumeFeeder(entries);
        Thread t = new Thread(() -> {
            try {
                feeder.run();
            } finally {
                replayFinished(feeder);
            }
        }, "resume-feeder");
        t.setDaemon(true);
        t.start();
    }

    /** Round NET-RESUME: replayDone, a back guest's mute and its welcome back change together under this lock, so a guest who
     *  joins just as the play-back ends is either welcomed by replayFinished or by resumeJoin, never muted for good. */
    static final Object RESUME_LOCK = new Object();

    static void replayFinished(ResumeFeeder feeder) {
        System.err.println("nethost: play-back done: " + feeder.applied + " applied, " + feeder.skipped + " skipped"
                + (feeder.diverged != null ? "; stopped early (" + feeder.diverged + ")" : ""));
        JsonObject m = new JsonObject();
        m.addProperty("t", "replay_done");
        m.addProperty("applied", feeder.applied);
        m.addProperty("skipped", feeder.skipped);
        m.addProperty("total", feeder.entries.size());
        if (feeder.diverged != null) {
            m.addProperty("diverged", feeder.diverged);
        }
        m.add("out", markOutSeats(feeder.diverged == null ? feeder.entries : List.of()));   // stopped early: only who lost                        // round RESUME2: the saved guests nobody waits for
        // Round NET-RESUME (O1, the online soak of 24 Oct, games 5 and 7): all of this used to run in one invokeLater, queued on
        // Swing's thread behind the play-back's last click. When that click makes Forge ask something there ("Choose an
        // ability", ...), Swing's thread waits for the answer, and the welcome back and the re-asking that would let anyone
        // give it never ran: the friend got no snapshot at all and the host waited for the friend. Done here instead, on the
        // feeder's thread: the host's table is told first that the play-back is over (so it stops ignoring questions), then
        // everything still waiting is asked again. A question Forge asks after this goes out as usual.
        synchronized (RESUME_LOCK) {
            replayDone = true;
            hostWire.send(m);
            BridgeGui hg = hostGui;
            if (hg != null) {
                hg.markDirty();
            }
            hostWire.resendPending();                        // what Forge is asking the host right now
            for (SeatWire g : guests) {
                if (g.attached()) {
                    welcomeBack(g);
                }
            }
        }
    }

    /** Round RESUME2 (Karl, 8 Nov: "skip guests who are out"): at the end of a play-back, every saved guest whose player has
     *  lost (it left the pod and was conceded for, or lost the ordinary way) is marked out: the host's table doesn't wait for it
     *  (HostSession.everyone_back reads this list from replay_done), the host's Cancel doesn't count it, and a guest coming back
     *  under another name takes a seat that is still in the game first. A guest who had dropped or left but whose player has NOT
     *  lost (a drop in a 1v1, or a guest gone before its concede happened) is still waited for, as before. The out guest may
     *  still join again and watch its seat. A seat counts as out when the saved journal has a concede for it (its own, or the
     *  one NetHost made when it left) or when its player has lost by now - the journal too, because the play-back's last
     *  commands run on Swing's thread after the feeder is done, and a concede waits for a safe moment (Main.concedeWhenSafe).
     *  Returns [{"seat","name"}, ...]. */
    static com.google.gson.JsonArray markOutSeats(List<ResumeFeeder.Entry> entries) {
        java.util.Set<Integer> conceded = new java.util.HashSet<>();
        for (ResumeFeeder.Entry e : entries) {
            JsonElement c = e.cmd.get("c");
            if (c != null && c.isJsonPrimitive() && "concede".equals(c.getAsString())) {
                conceded.add(e.seat);
            }
        }
        com.google.gson.JsonArray out = new com.google.gson.JsonArray();
        for (SeatWire g : guests) {
            if (conceded.contains(g.seat) || playerLost(g.gui)) {
                g.out = true;
                JsonObject o = new JsonObject();
                o.addProperty("seat", g.seat);
                o.addProperty("name", g.name);
                out.add(o);
                System.err.println("nethost: " + g.name + " is out of the saved game - not waiting for them");
            }
        }
        return out;
    }

    /** Round RESUME2: this seat's player has lost (conceded, or lost the ordinary way). */
    static boolean playerLost(BridgeGui g) {
        try {
            IGameController gc = g == null ? null : g.getGameController();
            return gc instanceof forge.game.player.PlayerController pc && pc.getPlayer() != null && pc.getPlayer().hasLost();
        } catch (Throwable t) {
            return false;
        }
    }

    /** A guest of a resumed game, once the play-back is done: the game as it is now, and whatever it is being asked.
     *  Safe on any thread (round NET-RESUME: never on Swing's, which a question may be holding). */
    static void welcomeBack(SeatWire seat) {
        seat.muted = false;
        seat.send(ready(false));
        BridgeGui g = seat.gui;
        if (g != null) {
            g.markDirty();
        }
        seat.resendPending();
    }

    /** Round MP2b: someone joining a resumed game takes back the seat with their name (or, if they changed it, the first free
     *  one). Their deck is the saved one, whatever they chose this time. */
    static void resumeJoin(SSLSocket s, Writer out, LineReader reader, JsonObject hello) throws IOException {
        String name = cleanName(str(hello, "name"));
        SeatWire seat = null;
        synchronized (NetHost.class) {
            for (SeatWire g : guests) {
                if (!g.attached() && !g.gone.get() && g.name.equalsIgnoreCase(name)) {
                    seat = g;
                    break;
                }
            }
            for (int pass = 0; seat == null && pass < 2; pass++) {
                for (SeatWire g : guests) {                   // round RESUME2: a seat still in the game first, an out one last
                    if (!g.attached() && !g.gone.get() && (pass == 1 || !g.out)) {
                        seat = g;
                        break;
                    }
                }
            }
        }
        if (seat == null) {
            noteRefusal("full");
            refuse(s, out, "full", "Everyone in this saved game is back already.");
            return;
        }
        final SeatWire taken = seat;
        s.setSoTimeout(0);
        JsonObject w = welcome(taken, false);
        w.addProperty("resumed", true);
        out.write(w + "\n");
        out.flush();
        taken.code = str(hello, "code");
        boolean welcomeNow;
        synchronized (RESUME_LOCK) {                          // round NET-RESUME: attach + mute under the play-back's lock
            taken.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(taken, cmd), NetHost::guestConnectionEnded);
            welcomeNow = replayDone;
            if (!welcomeNow) {
                taken.muted = true;                           // the play-back's questions are not theirs to answer
            }
        }
        startHeartbeat();
        int back = 0;
        for (SeatWire g : guests) {
            back += g.attached() ? 1 : 0;
        }
        JsonObject joined = new JsonObject();
        joined.addProperty("t", "guest_joined");
        joined.addProperty("name", taken.name);
        joined.addProperty("code", taken.code);
        joined.addProperty("seat", taken.seat);
        joined.addProperty("joined", back);
        joined.addProperty("wanted", guestsWanted);
        joined.addProperty("resumed", true);
        hostWire.send(joined);
        System.err.println("nethost: " + taken.name + " is back for the saved game (" + back + " of " + guestsWanted + ")");
        if (welcomeNow) {
            synchronized (RESUME_LOCK) {
                welcomeBack(taken);                           // round NET-RESUME: not queued on Swing's thread (see replayFinished)
            }
        } else {
            JsonObject m = new JsonObject();
            m.addProperty("t", "lobby");
            com.google.gson.JsonArray names = new com.google.gson.JsonArray();
            names.add(hostName);
            for (SeatWire g : guests) {
                names.add(g.name);
            }
            m.add("players", names);
            m.addProperty("wanted", guestsWanted);
            m.addProperty("joined", back);
            m.addProperty("ai", aiDeckPaths.size());
            m.addProperty("resuming", true);
            taken.sendNow(m);
        }
    }

    static SSLServerSocket openServer(String keystore, char[] pass, String bind, int port) throws Exception {
        KeyStore ks = KeyStore.getInstance("PKCS12");
        try (FileInputStream f = new FileInputStream(keystore)) {
            ks.load(f, pass);
        }
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(ks, pass);
        SSLContext ctx = SSLContext.getInstance("TLSv1.3");
        ctx.init(kmf.getKeyManagers(), null, null);
        SSLServerSocket ss = (SSLServerSocket) ctx.getServerSocketFactory().createServerSocket(port, 16, InetAddress.getByName(bind));
        ss.setEnabledProtocols(new String[]{"TLSv1.3"});    // nothing older; a plain-text client fails the handshake
        ss.setNeedClientAuth(false);
        return ss;
    }

    static void hostFailed(String reason, String text) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "host_failed");
        m.addProperty("reason", reason);
        m.addProperty("text", text);
        hostWire.send(m);
        System.err.println("nethost: " + text);
        Upnp.stop();
        System.exit(1);
    }

    // ---- the host's own table ----------------------------------------------------------------------------------------
    static void hostCommand(JsonObject cmd) {
        String c = cmd.get("c").getAsString();
        if (c.equals("cancel_host")) {
            boolean everyoneBack = true;
            for (SeatWire g : guests) {
                everyoneBack &= g.attached() || g.out;          // round RESUME2: nobody waits for a guest who is out
            }
            if (!started || (!resumeGuests.isEmpty() && !everyoneBack)) {   // round MP2b: a resumed game not under way yet
                JsonObject bye = new JsonObject();             // round MP2c: guests already waiting hear that it's off
                bye.addProperty("t", "peer_left");
                bye.addProperty("who", "host");
                bye.addProperty("name", hostName);
                bye.addProperty("cancelled", true);
                for (SeatWire g : guests) {
                    g.send(bye);
                }
                for (SeatWire g : guests) {
                    g.drain(1000);
                }
                JsonObject m = new JsonObject();
                m.addProperty("t", "hosting_cancelled");
                hostWire.send(m);
                exit(0);
            }
            return;
        }
        if (c.equals("quit")) {
            hostLeft();
            return;
        }
        if (draftMode && !started) {                         // round CD5: the draft's own commands, before the game
            switch (c) {
                case "draft_lock": draftLock(cmd); return;
                case "draft_send": draftSend(cmd); return;
                case "draft_game": draftGame(cmd); return;
                default: break;
            }
        }
        BridgeGui g = hostGui;
        if (g == null) {
            System.err.println("nethost: no game yet for the host's command " + c);
            return;
        }
        if (!replayDone && !c.equals("flush")) {
            return;                                           // round MP2b: the saved game is being played back
        }
        javax.swing.SwingUtilities.invokeLater(() -> {
            if (!c.equals("flush")) {
                journal(1, cmd);                              // round MP2b
            }
            Main.handle(g, cmd);
        });
    }

    /** The host's table closed (stdin ended) or quit: tell every guest and anyone watching, take the router mapping down, and stop. */
    static void hostLeft() {
        if (!leaving.compareAndSet(false, true)) {
            return;
        }
        JsonObject m = new JsonObject();
        m.addProperty("t", "peer_left");
        m.addProperty("who", "host");
        m.addProperty("name", hostName);
        m.addProperty("players", totalPlayers());
        List<SeatWire> remotes = new ArrayList<>(guests);
        remotes.addAll(watchers);
        for (SeatWire w : remotes) {
            if (!w.isClosed()) {
                w.send(m);
            }
        }
        for (SeatWire w : remotes) {
            w.drain(1500);
        }
        exit(0);
    }

    static void exit(int status) {
        Upnp.stop();
        System.exit(status);
    }

    /** Everyone at the table: the host, the guests it waits for (or has) and the AI seats. */
    static int totalPlayers() {
        return 1 + guestsWanted + aiDeckPaths.size();
    }

    // ---- guests arriving ---------------------------------------------------------------------------------------------
    static void acceptLoop(SSLServerSocket server) {
        // At most four handshakes at once, each limited to HELLO_SECONDS: one slow or silent visitor can't hold the door.
        ThreadPoolExecutor pool = new ThreadPoolExecutor(0, 4, 30, TimeUnit.SECONDS, new SynchronousQueue<>(), r -> {
            Thread t = new Thread(r, "nethost-hello");
            t.setDaemon(true);
            return t;
        });
        while (true) {
            Socket s;
            try {
                s = server.accept();
            } catch (IOException e) {
                System.err.println("nethost: accept failed: " + e);
                continue;
            }
            String addr = s.getInetAddress().getHostAddress();
            Long until = blockedUntil.get(addr);
            if (until != null && System.currentTimeMillis() < until) {
                closeQuietly(s);                                  // ignored: too many wrong passwords from this address
                continue;
            }
            try {
                pool.execute(() -> handshake((SSLSocket) s, addr));
            } catch (RejectedExecutionException e) {
                closeQuietly(s);
            }
        }
    }

    static void handshake(SSLSocket s, String addr) {
        AtomicBoolean finished = new AtomicBoolean(false);
        java.util.concurrent.ScheduledFuture<?> deadline = timers.schedule(() -> {
            if (!finished.get()) {
                System.err.println("nethost: no hello from " + addr + " within " + HELLO_SECONDS + " s");
                closeQuietly(s);
            }
        }, HELLO_SECONDS, TimeUnit.SECONDS);
        try {
            s.setTcpNoDelay(true);
            s.setSoTimeout(HELLO_SECONDS * 1000);
            s.startHandshake();
            LineReader reader = new LineReader(s.getInputStream(), HELLO_MAX);
            Writer out = new OutputStreamWriter(s.getOutputStream(), StandardCharsets.UTF_8);
            String line = reader.readLine();
            if (line == null) {
                closeQuietly(s);
                return;
            }
            if (line == LineReader.TOO_LONG) {
                refuse(s, out, "deck", "Your deck is too large to send (over " + DECK_MAX / 1024 + " KB).");
                return;
            }
            JsonObject hello;
            try {
                hello = JsonParser.parseString(line).getAsJsonObject();
            } catch (Exception e) {
                System.err.println("nethost: a hello that isn't JSON from " + addr);
                closeQuietly(s);
                return;
            }
            String kind = str(hello, "c");
            if (!kind.equals("hello") && !kind.equals("rejoin") && !kind.equals("watch")) {
                closeQuietly(s);
                return;
            }
            int v = hello.has("v") ? safeInt(hello.get("v")) : 0;
            if (v != PROTOCOL_V) {
                refuse(s, out, "version", "host speaks v" + PROTOCOL_V);
                return;
            }
            if (kind.equals("rejoin")) {                          // round MP2: a guest's table coming back
                rejoin(s, out, reader, hello, addr);
                return;
            }
            byte[] given = str(hello, "password").getBytes(StandardCharsets.UTF_8);
            if (!MessageDigest.isEqual(password.getBytes(StandardCharsets.UTF_8), given)) {   // constant time
                wrongTry(addr);
                noteRefusal("password");
                refuse(s, out, "password", "");
                return;
            }
            wrong.remove(addr);
            if (kind.equals("watch")) {                           // round MP2: a spectator
                watch(s, out, reader, hello);
                return;
            }
            if (!resumeGuests.isEmpty()) {                        // round MP2b: a saved game - the seats are already there
                finished.set(true);
                deadline.cancel(false);
                resumeJoin(s, out, reader, hello);
                return;
            }
            if (draftMode) {                                      // round CD5: a friend joining the draft (no deck yet)
                finished.set(true);
                deadline.cancel(false);
                draftJoin(s, out, reader, hello);
                return;
            }
            JsonObject deck = hello.has("deck") && hello.get("deck").isJsonObject() ? hello.getAsJsonObject("deck") : new JsonObject();
            String dck = str(deck, "dck");
            if (dck.getBytes(StandardCharsets.UTF_8).length > DECK_MAX) {
                noteRefusal("deck");
                refuse(s, out, "deck", "Your deck is too large to send (over " + DECK_MAX / 1024 + " KB).");
                return;
            }
            // Round MP2c: a seat is kept for this guest while its deck is checked; one more than the host asked for is "full".
            SeatWire seat;
            synchronized (NetHost.class) {
                if (started || guests.size() >= guestsWanted) {
                    noteRefusal("full");
                    refuse(s, out, "full", guestsWanted == 1 ? "" : "That game already has all its players.");
                    return;
                }
                seat = new SeatWire(freeSeatNumber(), uniqueName(cleanName(str(hello, "name"))), newToken(), REMOTE_RATE);
                seat.deckPath = seatDeckPath(seat.seat);
                seat.code = str(hello, "code");
                guests.add(seat);
            }
            String problem = deckProblem(dck, seat.deckPath);
            if (problem != null) {
                guests.remove(seat);
                noteRefusal("deck");
                refuse(s, out, "deck", problem);
                return;
            }
            finished.set(true);
            deadline.cancel(false);
            s.setSoTimeout(0);
            out.write(welcome(seat, false) + "\n");
            out.flush();
            seat.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(seat, cmd), NetHost::guestConnectionEnded);
            startHeartbeat();
            JsonObject joined = new JsonObject();
            joined.addProperty("t", "guest_joined");
            joined.addProperty("name", seat.name);
            joined.addProperty("code", seat.code);
            joined.addProperty("seat", seat.seat);
            joined.addProperty("joined", guests.size());
            joined.addProperty("wanted", guestsWanted);
            hostWire.send(joined);
            System.err.println("nethost: " + seat.name + " joined (" + guests.size() + " of " + guestsWanted + ")");
            boolean full;
            synchronized (NetHost.class) {
                full = !started && guests.size() == guestsWanted;
                if (full) {
                    started = true;
                }
            }
            if (full) {
                startGame();
            } else {
                sendLobby();
            }
        } catch (Exception e) {
            System.err.println("nethost: handshake with " + addr + " failed: " + e);
            closeQuietly(s);
        } finally {
            finished.set(true);
            deadline.cancel(false);
        }
    }

    static int freeSeatNumber() {
        for (int n = 2; ; n++) {
            boolean used = false;
            for (SeatWire g : guests) {
                used |= g.seat == n;
            }
            if (!used) {
                return n;
            }
        }
    }

    /** Forge keys players by name in places: keep every name at the table apart. */
    static String uniqueName(String name) {
        java.util.Set<String> taken = new java.util.HashSet<>();
        taken.add(hostName.toLowerCase());
        for (SeatWire g : guests) {
            taken.add(g.name.toLowerCase());
        }
        String n = name;
        for (int k = 2; taken.contains(n.toLowerCase()); k++) {
            n = name + " (" + k + ")";
        }
        return n;
    }

    /** guest.dck for the first guest's seat (2), as in MP1; guest_3.dck and guest_4.dck for the others. */
    static String seatDeckPath(int seat) {
        if (seat == 2) {
            return guestDeckPath;
        }
        String p = guestDeckPath;
        int dot = p.lastIndexOf('.');
        return dot > p.lastIndexOf(File.separatorChar) ? p.substring(0, dot) + "_" + seat + p.substring(dot) : p + "_" + seat;
    }

    /** Round MP2c: who is at the table so far, to every guest waiting for the others. */
    static void sendLobby() {
        JsonObject m = new JsonObject();
        m.addProperty("t", "lobby");
        com.google.gson.JsonArray names = new com.google.gson.JsonArray();
        names.add(hostName);
        for (SeatWire g : guests) {
            names.add(g.name);
        }
        m.add("players", names);
        m.addProperty("wanted", guestsWanted);
        m.addProperty("joined", guests.size());
        m.addProperty("ai", aiDeckPaths.size());
        for (SeatWire g : guests) {
            g.send(m);
        }
        hostWire.send(m);
    }

    static void wrongTry(String addr) {
        int n = wrong.computeIfAbsent(addr, k -> new int[1])[0] += 1;
        if (n >= WRONG_TRIES) {
            wrong.remove(addr);
            blockedUntil.put(addr, System.currentTimeMillis() + blockSeconds * 1000L);
            System.err.println("nethost: " + WRONG_TRIES + " wrong passwords from " + addr + "; ignoring it for "
                    + blockSeconds + " s");
        }
    }

    static String newToken() {
        byte[] b = new byte[16];
        new java.security.SecureRandom().nextBytes(b);
        StringBuilder sb = new StringBuilder();
        for (byte x : b) {
            sb.append(String.format("%02x", x));
        }
        return sb.toString();
    }

    static JsonObject welcome(SeatWire seat, boolean rejoined) {
        JsonObject w = new JsonObject();
        w.addProperty("t", "welcome");
        w.addProperty("v", PROTOCOL_V);
        w.addProperty("host", hostName);
        w.addProperty("seat", seat.seat);
        w.addProperty("code", code);
        w.addProperty("token", seat.token);
        w.addProperty("grace", graceSeconds);
        w.addProperty("players", totalPlayers());              // round MP2c: the whole table, AI seats included
        w.addProperty("guests", guestsWanted);
        addFormat(w);                                          // round MP3a
        if (draftMode && !started) {                           // round CD5: the table opens the draft, and retries for long
            w.addProperty("draft", true);
            w.addProperty("grace", draftReconnectSeconds);
        }
        if (rejoined) {
            w.addProperty("rejoined", true);
        }
        return w;
    }

    /** Round MP3a: the game's format (the deck screen's name for it) and its house rules, for the welcome and ready lines. */
    static void addFormat(JsonObject o) {
        o.addProperty("gameFormat", gameFormat);
        com.google.gson.JsonArray rules = new com.google.gson.JsonArray();
        for (String r : HouseRules.active) rules.add(r);
        o.add("houseRules", rules);
    }

    static JsonObject ready(boolean spectator) {
        JsonObject ready = new JsonObject();
        ready.addProperty("t", "ready");
        ready.addProperty("protocol", 2);
        ready.addProperty("seats", totalPlayers());
        ready.addProperty("online", true);
        ready.addProperty("aiTimeout", Main.aiTimeout);
        ready.addProperty("loopCap", LoopGuard.cap);
        ready.addProperty("threatAi", Threats.on);           // round AI2
        ready.addProperty("grace", graceSeconds);
        ready.addProperty("format", Main.format);              // round MP3a: how Forge plays it, as Main's ready line says
        addFormat(ready);
        if (spectator) {
            ready.addProperty("spectator", true);
        }
        return ready;
    }

    static SeatWire seatWithToken(String token) {
        byte[] given = token.getBytes(StandardCharsets.UTF_8);
        SeatWire found = null;
        for (SeatWire g : guests) {                             // every token is compared, in constant time
            if (MessageDigest.isEqual(g.token.getBytes(StandardCharsets.UTF_8), given) && !token.isEmpty()) {
                found = g;
            }
        }
        return found;
    }

    /** Round MP2: a guest's table reconnecting with its token. On success the new connection replaces the old one. */
    static void rejoin(SSLSocket s, Writer out, LineReader reader, JsonObject hello, String addr) throws IOException {
        SeatWire seat = seatWithToken(str(hello, "token"));
        if (seat == null) {
            wrongTry(addr);
            noteRefusal("rejoin");
            refuse(s, out, "rejoin", "This game does not know that seat.");
            return;
        }
        BridgeGui sgui = seat.gui;
        boolean overForSeat = sgui != null && sgui.gameOverMsg != null;   // round RESUME2: Forge has finished it and said so
        if (seat.gone.get() || leaving.get() || (gameOver && !overForSeat)) {
            refuse(s, out, "gone", "That game has ended.");
            return;
        }
        wrong.remove(addr);
        s.setSoTimeout(0);
        out.write(welcome(seat, true) + "\n");
        out.flush();
        synchronized (seat.announce) {                          // round MP2e: after any "dropped" of the old connection
            JsonObject missedOver;
            synchronized (sgui != null ? sgui.gameOverLock : seat.announce) {   // round RESUME2: see the note above watch()
                seat.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(seat, cmd), NetHost::guestConnectionEnded);
                missedOver = sgui != null ? sgui.gameOverMsg : null;     // went out before this connection existed
            }
            java.util.concurrent.ScheduledFuture<?> g = seat.graceTask;
            if (g != null) {
                g.cancel(false);
            }
            if (!started) {
                if (draftMode && draftLocked) {                 // round CD5: back in the draft - the host's table carries on
                    hostWire.send(draftBack(seat, "token"));
                    System.err.println("nethost: " + seat.name + " is back in the draft");
                    return;
                }
                sendLobby();                                    // still waiting for the others
                return;
            }
            boolean stillPlayingBack;
            synchronized (RESUME_LOCK) {                        // round NET-RESUME: never muted after replayFinished unmuted
                stillPlayingBack = !replayDone;
                if (stillPlayingBack) {
                    seat.muted = true;
                }
            }
            if (stillPlayingBack) {                             // round MP2b: a saved game still being played back
                JsonObject m = new JsonObject();
                m.addProperty("t", "lobby");
                m.addProperty("resuming", true);
                m.addProperty("wanted", guestsWanted);
                seat.sendNow(m);
                return;
            }
            seat.send(ready(false));
            boolean resent = missedOver != null;               // round RESUME2: the game ended while it was away
            if (resent) {
                seat.send(missedOver);
            }
            System.err.println("nethost: " + seat.name + " reconnected (" + seat.pendingCount() + " question(s) asked again"
                    + (resent ? "; game over sent again" : "") + ")");
            JsonObject back = new JsonObject();
            back.addProperty("t", "peer_back");
            back.addProperty("who", "guest");
            back.addProperty("name", seat.name);
            back.addProperty("seat", seat.seat);
            tellAll(back, seat);
        }
        // Round NET-RESUME: not queued on Swing's thread. A question Forge asked there (a click's "Choose an ability") holds
        // that thread until the answer comes, and the answer can only come once the question is asked again on the new
        // connection; queued behind it, a reconnect in that moment would wait for ever (read from the code, not reproduced).
        BridgeGui gg = seat.gui;
        if (gg != null) {
            gg.markDirty();
        }
        seat.resendPending();
    }

    // Round RESUME2 (RESUME1's 1 h soak, game 23: online_mismatch "game over on host only"): a guest whose connection was cut
    // just as the game ended came back to a snapshot that said gameOver, but the game_over message had gone out while it had no
    // connection (SeatWire drops lines then), and nobody sent it again. rejoin now attaches the new connection and reads
    // BridgeGui.gameOverMsg under BridgeGui.gameOverLock, the lock finishGame sends it under: a game_over that went out before
    // the new connection existed is sent again on it; one sent after goes out on the new connection by itself - never twice on
    // one connection. A rejoin to a game Forge has already finished for that seat is no longer refused ("gone"). The guest's
    // table shows its end screen once either way (ForgeTable.shown_over).

    /** Round MP2: a spectator. Gets an all-face-down view of every player's hidden cards and may not play. */
    static void watch(SSLSocket s, Writer out, LineReader reader, JsonObject hello) throws IOException {
        if (!started || !replayDone || Main.match == null || Main.match.getGame() == null) {
            noteRefusal("not_started");
            refuse(s, out, "not_started", "The game hasn't started yet. Try again once the host's game is under way.");
            return;
        }
        if (gameOver || leaving.get()) {
            refuse(s, out, "gone", "That game has ended.");
            return;
        }
        if (watchers.size() >= MAX_SPECTATORS) {
            noteRefusal("full");
            refuse(s, out, "full", "This game already has " + MAX_SPECTATORS + " people watching.");
            return;
        }
        String name = cleanName(str(hello, "name"));
        SeatWire w = new SeatWire(0, name, newToken(), REMOTE_RATE);
        s.setSoTimeout(0);
        JsonObject welcome = new JsonObject();
        welcome.addProperty("t", "welcome");
        welcome.addProperty("v", PROTOCOL_V);
        welcome.addProperty("host", hostName);
        StringBuilder names = new StringBuilder();
        for (SeatWire g : guests) {
            names.append(names.length() == 0 ? "" : ", ").append(g.name);
        }
        welcome.addProperty("guest", names.toString());
        welcome.addProperty("players", totalPlayers());
        welcome.addProperty("watching", true);
        addFormat(welcome);                                    // round MP3a
        welcome.addProperty("code", code);
        out.write(welcome + "\n");
        out.flush();
        BridgeGui sg = new BridgeGui(w);
        sg.spectator = true;
        sg.events = new EventForwarder(w);
        watchers.add(w);
        w.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> watcherCommand(w, sg, cmd), (sw, c) -> {
            if (sw.detach(c)) {
                watcherLeft(sw);
            }
        });
        w.send(ready(true));
        javax.swing.SwingUtilities.invokeLater(() -> {
            try {
                sg.setGameView(Main.match.getGameView());
                Main.match.registerSpectator(sg);
            } catch (Throwable t) {
                System.err.println("nethost: could not add the spectator to the game: " + t);
            }
            sg.markDirty();
        });
        System.err.println("nethost: " + name + " is watching");
        JsonObject m = new JsonObject();
        m.addProperty("t", "spectator_joined");
        m.addProperty("name", name);
        tellAll(m, w);
    }

    static void watcherCommand(SeatWire w, BridgeGui sg, JsonObject cmd) {
        String c = str(cmd, "c");
        switch (c) {
            case "flush": sg.markDirty(); return;
            case "hb": return;
            case "leave": w.dropConnection("the spectator left"); return;
            default:
                JsonObject m = new JsonObject();
                m.addProperty("t", "refused_cmd");
                m.addProperty("c", c.length() > 40 ? c.substring(0, 40) : c);
                w.send(m);
        }
    }

    static void watcherLeft(SeatWire w) {
        if (!watchers.remove(w)) {
            return;
        }
        w.close();
        System.err.println("nethost: " + w.name + " stopped watching");
        if (leaving.get()) {
            return;
        }
        JsonObject m = new JsonObject();
        m.addProperty("t", "spectator_left");
        m.addProperty("name", w.name);
        tellAll(m, w);
    }

    /** One message to the host's table, every guest's (when connected) and every spectator but `except`. */
    static void tellAll(JsonObject m, SeatWire except) {
        hostWire.send(m);
        for (SeatWire g : guests) {
            if (g != except && !g.isClosed()) {
                g.send(m);
            }
        }
        for (SeatWire w : watchers) {
            if (w != except) {
                w.send(m);
            }
        }
    }

    /** Round MP2: every few seconds, a "hb" line to each remote table; a connection silent for too long is dropped (a dead
     *  link that never closed), which for a guest starts its grace period. Runs from the first guest's arrival. */
    static void startHeartbeat() {
        if (!heartbeatOn.compareAndSet(false, true)) {
            return;
        }
        timers.scheduleAtFixedRate(() -> {
            try {
                JsonObject hb = new JsonObject();
                hb.addProperty("t", "hb");
                long now = System.currentTimeMillis();
                List<SeatWire> remotes = new ArrayList<>(guests);
                remotes.addAll(watchers);
                for (SeatWire w : remotes) {
                    if (!w.attached() || w.isClosed()) {
                        continue;
                    }
                    if (now - w.lastHeard() > silenceSeconds * 1000L) {
                        w.dropConnection("nothing heard for " + silenceSeconds + " s");
                    } else if (w.muted) {
                        w.sendNow(hb);                        // round MP2b: waiting for a play-back, still alive
                    } else {
                        w.send(hb);
                    }
                }
            } catch (Throwable t) {
                System.err.println("nethost: heartbeat: " + t);
            }
        }, heartbeatSeconds, heartbeatSeconds, TimeUnit.SECONDS);
    }

    /** Null when the guest's deck can be played, else what's wrong with it, in plain words. Written to `path` (its seat's file). */
    static String deckProblem(String dck, String path) {
        if (dck.isBlank()) {
            return "No deck was sent.";
        }
        try {
            File f = new File(path);
            File dir = f.getAbsoluteFile().getParentFile();
            if (dir != null) {
                dir.mkdirs();
            }
            try (Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) {
                w.write(dck);
            }
            Deck d = DeckSerializer.fromFile(f);
            if (d == null) {
                return "Forge could not read the deck.";
            }
            // Round MP3a: the deck must be of this game's format (as the deck screen groups them), then that format's checks.
            String fmt = OnlineFormats.ofDck(dck);
            if (!OnlineFormats.compatible(gameFormat, fmt)) {
                return OnlineFormats.mismatch(gameFormat, fmt);
            }
            if (OnlineFormats.hasCommander(gameFormat)) {
                if (d.getCommanders().isEmpty()) {
                    return "The deck has no commander.";
                }
                if (d.getMain().countAll() < 1) {
                    return "The deck has no cards besides the commander.";
                }
            } else if (d.getMain().countAll() < 1) {
                return "The deck has no cards.";
            }
            return null;
        } catch (Exception e) {
            String t = String.valueOf(e);
            return "Forge could not read the deck: " + (t.length() > 300 ? t.substring(0, 300) : t);
        }
    }

    static String cleanName(String raw) {
        StringBuilder b = new StringBuilder();
        for (char ch : raw.toCharArray()) {
            if (!Character.isISOControl(ch) && ch != '<' && ch != '>') {     // no control characters, no Forge markup
                b.append(ch);
            }
        }
        String n = b.toString().trim().replaceAll("\\s+", " ");
        if (n.length() > NAME_MAX) {
            n = n.substring(0, NAME_MAX).trim();
        }
        return n.isEmpty() ? "Guest" : n;
    }

    static void refuse(Socket s, Writer out, String reason, String text) {
        try {
            JsonObject m = new JsonObject();
            m.addProperty("t", "refused");
            m.addProperty("reason", reason);
            if (text != null && !text.isEmpty()) {
                m.addProperty("text", text);
            }
            out.write(m + "\n");
            out.flush();
        } catch (IOException ignored) {
        }
        closeQuietly(s);
    }

    static void noteRefusal(String reason) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "join_refused");
        m.addProperty("reason", reason);
        hostWire.send(m);
    }

    // ---- the game ----------------------------------------------------------------------------------------------------
    /** Everyone the host waited for has joined: the host, each guest and each AI seat, one Forge game. */
    static void startGame() throws Exception {
        List<RegisteredPlayer> players = new ArrayList<>();
        Map<RegisteredPlayer, IGuiGame> guis = new LinkedHashMap<>();
        // Round MP3a: every seat is registered as Main registers it for this format (Main.register: forCommander, Brawl's
        // forVariants with the table's size, or a plain Constructed seat), and the match starts as Main starts it.
        int seatCount = 1 + guests.size() + aiDecks.size();
        RegisteredPlayer p1 = Main.register(hostDeck, seatCount);
        p1.setPlayer(new Passing.Human(hostName));          // patch 43
        BridgeGui g1 = new BridgeGui(hostWire);
        g1.events = new EventForwarder(hostWire);
        players.add(p1);
        guis.put(p1, g1);
        for (SeatWire seat : guests) {
            Deck d = DeckSerializer.fromFile(new File(seat.deckPath));
            RegisteredPlayer rp = Main.register(d, seatCount);
            rp.setPlayer(new Passing.Human(seat.name));         // patch 43
            BridgeGui g = new BridgeGui(seat);
            g.events = new EventForwarder(seat);
            seat.gui = g;
            players.add(rp);
            guis.put(rp, g);
        }
        int i = 1;
        for (Deck d : aiDecks) {                                  // round MP2c: AI seats, named as in a game against the AIs
            RegisteredPlayer rp = Main.register(d, seatCount);
            rp.setPlayer(LoopGuard.guarded(GamePlayerUtil.createAiPlayer("AI " + i + " (" + d.getName() + ")", i, "")));
            players.add(rp);
            i++;
        }
        hostGui = g1;
        guestName = guests.isEmpty() ? null : guests.get(0).name;
        // Round MP2b: who sits where, for the host's save; and every reply goes into the journal
        JsonObject seats = new JsonObject();
        seats.addProperty("t", "seats");
        com.google.gson.JsonArray list = new com.google.gson.JsonArray();
        for (SeatWire seat : guests) {
            JsonObject o = new JsonObject();
            o.addProperty("seat", seat.seat);
            o.addProperty("name", seat.name);
            o.addProperty("deck", seat.deckPath);
            list.add(o);
        }
        seats.add("guests", list);
        hostWire.send(seats);
        hostWire.replyListener = cmd -> journal(1, cmd);
        for (SeatWire seat : guests) {
            seat.replyListener = cmd -> journal(seat.seat, cmd);
        }
        hostWire.send(ready(false));
        for (SeatWire seat : guests) {
            seat.send(ready(false));
        }
        Main.match = new HostedMatch();
        Main.match.setOnMatchOver(() -> gameOver = true);
        // The Commander variant must be applied (round 27c: without it nobody is asked about moving a commander to the
        // command zone), exactly as Main does. Round MP3a: the format's variant (Brawl), or none for a Constructed game.
        GameType variant = Main.variant();
        java.util.Set<GameType> variants = Checks.fault(Checks.Fault.NO_COMMANDER_VARIANT) || Main.constructed()
                ? java.util.EnumSet.noneOf(GameType.class) : java.util.EnumSet.of(variant);
        Main.match.startMatch(variant, variants, players, guis);
        System.err.println("nethost: the game started with " + players.size() + " players (" + guests.size() + " online, "
                + aiDecks.size() + " AI), " + gameFormat + (HouseRules.active.isEmpty() ? "" : " + " + HouseRules.active));
    }

    // ---- round CD5: drafting together -------------------------------------------------------------------------------
    /** A friend joining the draft: a new seat while the lobby is open, or - once the draft has started - their own seat back,
     *  found by name (they lost the rejoin token: their program closed). Anyone else is turned away. */
    static void draftJoin(SSLSocket s, Writer out, LineReader reader, JsonObject hello) throws IOException {
        boolean drafting = flag(hello, "draft") || flag(hello, "drafts");   // a draft hello, or any hello of a copy that drafts
        if (!drafting) {                                      // a copy of the game from before online drafts
            noteRefusal("version");
            refuse(s, out, "version", "host is drafting (needs a copy with online drafts)");
            return;
        }
        String name = cleanName(str(hello, "name"));
        SeatWire seat = null;
        boolean back = false;
        String full = null;
        synchronized (NetHost.class) {
            if (started) {
                full = "The draft is over and its game has started.";
            } else if (draftLocked) {
                for (SeatWire g : guests) {
                    if (!g.attached() && !g.gone.get() && g.name.equalsIgnoreCase(name)) {
                        seat = g;
                        back = true;
                        break;
                    }
                }
                if (seat == null) {
                    full = "The draft has started without you: ask the host to start another one.";
                }
            } else if (guests.size() >= guestsWanted) {
                full = "This draft already has all its players.";
            } else {
                seat = new SeatWire(freeSeatNumber(), uniqueName(name), newToken(), REMOTE_RATE);
                seat.deckPath = seatDeckPath(seat.seat);
                guests.add(seat);
            }
            if (seat != null) {
                seat.code = str(hello, "code");
            }
        }
        if (seat == null) {
            noteRefusal("full");
            refuse(s, out, "draft_full", full);
            return;
        }
        final SeatWire taken = seat;
        s.setSoTimeout(0);
        out.write(welcome(taken, back) + "\n");
        out.flush();
        synchronized (taken.announce) {
            taken.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(taken, cmd), NetHost::guestConnectionEnded);
        }
        startHeartbeat();
        if (back) {
            System.err.println("nethost: " + taken.name + " is back in the draft (by name)");
            hostWire.send(draftBack(taken, "name"));
            return;
        }
        JsonObject joined = new JsonObject();
        joined.addProperty("t", "guest_joined");
        joined.addProperty("name", taken.name);
        joined.addProperty("code", taken.code);
        joined.addProperty("seat", taken.seat);
        joined.addProperty("joined", guests.size());
        joined.addProperty("wanted", guestsWanted);
        joined.addProperty("draft", true);
        hostWire.send(joined);
        System.err.println("nethost: " + taken.name + " joined the draft (" + guests.size() + " of at most " + guestsWanted + ")");
        sendLobby();
    }

    static boolean flag(JsonObject o, String key) {
        try {
            return o.has(key) && o.get(key).isJsonPrimitive() && o.get(key).getAsBoolean();
        } catch (Exception e) {
            return false;
        }
    }

    static JsonObject draftBack(SeatWire seat, String how) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "peer_back");
        m.addProperty("who", "guest");
        m.addProperty("name", seat.name);
        m.addProperty("seat", seat.seat);
        m.addProperty("draft", true);
        m.addProperty("by", how);
        return m;
    }

    /** The host pressed Start: no more friends join; the seats as they are. A continued draft (the host's program had closed)
     *  names the friends who aren't back yet in "reserve": each gets a seat now, empty, that they take with the same name. */
    static void draftLock(JsonObject cmd) {
        com.google.gson.JsonArray list = new com.google.gson.JsonArray();
        synchronized (NetHost.class) {
            if (cmd.has("reserve") && cmd.get("reserve").isJsonArray()) {
                for (JsonElement e : cmd.getAsJsonArray("reserve")) {
                    if (guests.size() >= 3) {
                        break;
                    }
                    String name = cleanName(e.isJsonPrimitive() ? e.getAsString() : "");
                    boolean taken = false;
                    for (SeatWire g : guests) {
                        taken |= g.name.equalsIgnoreCase(name);
                    }
                    if (!taken) {
                        SeatWire seat = new SeatWire(freeSeatNumber(), name, newToken(), REMOTE_RATE);
                        seat.deckPath = seatDeckPath(seat.seat);
                        guests.add(seat);
                    }
                }
            }
            draftLocked = true;
            guestsWanted = Math.max(1, guests.size());
            for (SeatWire g : guests) {
                JsonObject o = new JsonObject();
                o.addProperty("seat", g.seat);
                o.addProperty("name", g.name);
                o.addProperty("connected", g.attached());
                list.add(o);
            }
        }
        JsonObject m = new JsonObject();
        m.addProperty("t", "draft_locked");
        m.add("seats", list);
        hostWire.send(m);
        System.err.println("nethost: the draft started with " + list.size() + " friend(s)");
    }

    /** One draft message from the host's table to one friend's (dropped while that friend is away: the host sends it again). */
    static void draftSend(JsonObject cmd) {
        int seatNo = cmd.has("seat") ? safeInt(cmd.get("seat")) : -1;
        JsonElement msg = cmd.get("msg");
        if (msg == null || !msg.isJsonObject()) {
            return;
        }
        for (SeatWire g : guests) {
            if (g.seat == seatNo && !g.gone.get()) {
                JsonObject m = new JsonObject();
                m.addProperty("t", "draft");
                m.add("msg", msg);
                g.send(m);
                return;
            }
        }
    }

    /** Every deck is ready: the host's, each friend's who plays (their seats), and the AI seats' (the AI drafters', and any
     *  friend's seat the host gave to the AI). A friend not in "seats" gives up the seat. Then the game starts in this process. */
    static void draftGame(JsonObject cmd) {
        try {
            Deck mine = DeckSerializer.fromFile(new File(str(cmd, "host_deck")));
            if (mine == null) {
                draftGameFailed("could not read the host's deck");
                return;
            }
            Map<Integer, String> paths = new java.util.HashMap<>();
            if (cmd.has("seats") && cmd.get("seats").isJsonArray()) {
                for (JsonElement e : cmd.getAsJsonArray("seats")) {
                    JsonObject o = e.getAsJsonObject();
                    paths.put(safeInt(o.get("seat")), str(o, "deck"));
                }
            }
            List<String> ai = new ArrayList<>();
            List<Deck> aiD = new ArrayList<>();
            if (cmd.has("ai") && cmd.get("ai").isJsonArray()) {
                for (JsonElement e : cmd.getAsJsonArray("ai")) {
                    Deck d = DeckSerializer.fromFile(new File(e.getAsString()));
                    if (d == null) {
                        draftGameFailed("could not read the AI deck " + e.getAsString());
                        return;
                    }
                    ai.add(e.getAsString());
                    aiD.add(d);
                }
            }
            List<SeatWire> dropped = new ArrayList<>();
            synchronized (NetHost.class) {
                int playing = 0;
                for (SeatWire g : guests) {
                    playing += paths.containsKey(g.seat) ? 1 : 0;
                }
                if (1 + playing + aiD.size() > 4) {
                    draftGameFailed("more than 4 players");
                    return;
                }
                for (SeatWire g : new ArrayList<>(guests)) {
                    String path = paths.get(g.seat);
                    if (path == null) {
                        guests.remove(g);
                        g.gone.set(true);
                        dropped.add(g);
                    } else {
                        g.deckPath = path;
                    }
                }
                hostDeck = mine;
                aiDeckPaths.clear();
                aiDeckPaths.addAll(ai);
                aiDecks.clear();
                aiDecks.addAll(aiD);
                guestsWanted = guests.size();
                started = true;
            }
            for (SeatWire g : dropped) {
                g.close();
            }
            startGame();
        } catch (Exception e) {
            draftGameFailed(String.valueOf(e));
        }
    }

    static void draftGameFailed(String text) {
        System.err.println("nethost: the draft's game could not start: " + text);
        JsonObject m = new JsonObject();
        m.addProperty("t", "draft_game_failed");
        m.addProperty("text", text);
        hostWire.send(m);
    }

    static void guestCommand(SeatWire seat, JsonObject cmd) {
        String c = cmd.get("c").getAsString();
        if (c.equals("hb")) {
            return;                                           // round MP2: the heartbeat's answer (SeatWire noted the time)
        }
        if (c.equals("leave")) {                              // round MP2: the guest is going for good - no grace period
            seat.leaving = true;
            seat.dropConnection(seat.name + " left the game");
            return;
        }
        if (c.equals("draft") && draftMode && !started) {   // round CD5: a pick, a deck, ... - the host's table checks it
            JsonElement msg = cmd.get("msg");
            if (msg != null && msg.isJsonObject()) {
                JsonObject m = new JsonObject();
                m.addProperty("t", "draft_from");
                m.addProperty("seat", seat.seat);
                m.addProperty("name", seat.name);
                m.add("msg", msg);
                hostWire.send(m);
            }
            return;
        }
        if (!REMOTE_ALLOWED.contains(c)) {
            System.err.println("nethost: refused " + seat.name + "'s command " + c);
            JsonObject m = new JsonObject();
            m.addProperty("t", "refused_cmd");
            m.addProperty("c", c.length() > 40 ? c.substring(0, 40) : c);
            seat.send(m);
            return;
        }
        BridgeGui g = seat.gui;
        if (g == null || !replayDone) {
            return;                                           // round MP2c: still waiting for the others (MP2b: or the play-back)
        }
        javax.swing.SwingUtilities.invokeLater(() -> {
            if (!c.equals("flush")) {
                journal(seat.seat, cmd);                      // round MP2b
            }
            Main.handle(g, cmd);
        });
    }

    /** Tests only (--drop-notice-delay-ms): widens the moment between a connection's end and its announcement. */
    static void pauseForTests(int millis) {
        if (millis > 0) {
            try {
                Thread.sleep(millis);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    /** Round MP2: one of a guest's connections ended. If it was the current one, the seat waits for a reconnect for the grace
     *  period - unless the guest said it was leaving, the game is over, or reconnecting is off (--grace-seconds 0). A guest
     *  waiting for the others (the game not started) simply gives up its seat.
     *  Round MP2e: the detach and the "peer_dropped" happen under the seat's announce lock, which rejoin() holds while it
     *  attaches and says "peer_back": so "dropped" is never sent after "back" (FB1's full suite caught it once). Leaving the
     *  seat (guestLeft, lobbyLeft) happens after the lock is released. */
    static void guestConnectionEnded(SeatWire seat, RemoteWire c) {
        boolean lobby;
        synchronized (seat.announce) {
            if (!seat.detach(c)) {
                return;                                       // an old connection that a reconnect already replaced
            }
            if (leaving.get()) {
                return;
            }
            if (started && !replayDone) {
                return;                                       // round MP2b: the seat stays theirs; they can join again
            }
            if (draftMode && draftLocked && !started) {       // round CD5: the draft waits for them (or the host gives the seat
                String why = seat.dropReason() != null ? seat.dropReason() : "the connection closed";   // to the AI)
                JsonObject m = new JsonObject();
                m.addProperty("who", "guest");
                m.addProperty("name", seat.name);
                m.addProperty("seat", seat.seat);
                m.addProperty("why", why);
                m.addProperty("draft", true);
                if (seat.leaving) {                           // it said "leave": it won't be back
                    seat.gone.set(true);
                    m.addProperty("t", "peer_left");
                    m.addProperty("left", true);
                    m.addProperty("players", totalPlayers());
                    System.err.println("nethost: " + seat.name + " left the draft");
                } else {
                    m.addProperty("t", "peer_dropped");
                    m.addProperty("grace", 0);
                    System.err.println("nethost: " + seat.name + "'s connection dropped during the draft (" + why + "); the draft waits");
                }
                hostWire.send(m);                             // the host's table only: the others' draft screens hear it from it
                return;
            }
            lobby = !started;
            if (!lobby && !(gameOver || seat.leaving || graceSeconds <= 0 || Main.match == null)) {
                pauseForTests(dropNoticeDelayMs);
                String why = seat.dropReason() != null ? seat.dropReason() : "the connection closed";
                System.err.println("nethost: " + seat.name + "'s connection dropped (" + why + "); holding the seat for " + graceSeconds + " s");
                JsonObject m = new JsonObject();
                m.addProperty("t", "peer_dropped");
                m.addProperty("who", "guest");
                m.addProperty("name", seat.name);
                m.addProperty("seat", seat.seat);
                m.addProperty("why", why);
                m.addProperty("grace", graceSeconds);
                tellAll(m, seat);
                seat.graceTask = timers.schedule(() -> {
                    if (!seat.attached() && !gameOver) {
                        System.err.println("nethost: " + seat.name + " did not come back within " + graceSeconds + " s");
                        seat.expired = true;
                        guestLeft(seat);
                    }
                }, graceSeconds, TimeUnit.SECONDS);
                return;
            }
        }
        if (lobby) {
            lobbyLeft(seat);
        } else {
            guestLeft(seat);
        }
    }

    /** Round MP2c: a guest who was waiting for the others went away before the game started: the seat is free again. */
    static void lobbyLeft(SeatWire seat) {
        boolean removed;
        synchronized (NetHost.class) {
            removed = !started && guests.remove(seat);
        }
        if (!removed) {
            if (started) {
                guestLeft(seat);                              // the game started meanwhile
            }
            return;
        }
        seat.close();
        System.err.println("nethost: " + seat.name + " left before the game started");
        JsonObject m = new JsonObject();
        m.addProperty("t", "guest_left_lobby");
        m.addProperty("name", seat.name);
        m.addProperty("seat", seat.seat);
        m.addProperty("joined", guests.size());
        m.addProperty("wanted", guestsWanted);
        hostWire.send(m);
        sendLobby();
    }

    /** A guest is gone for good (it left, its grace period ran out, or the game was over). Every question Forge was waiting on
     *  from it fails (Wire.close); it concedes, so Forge finishes the game the normal way - or, in a game of three or more, the
     *  others play on - and the host's table gets game_over in the end. */
    static void guestLeft(SeatWire seat) {
        if (leaving.get()) {
            return;                                           // the host is closing anyway
        }
        if (!seat.gone.compareAndSet(false, true)) {
            return;
        }
        String why = seat.dropReason() != null ? seat.dropReason() : "the connection closed";
        seat.close();
        System.err.println("nethost: " + seat.name + " left (" + why + ")");
        JsonObject m = new JsonObject();
        m.addProperty("t", "peer_left");
        m.addProperty("who", "guest");
        m.addProperty("why", why);
        m.addProperty("gameOver", gameOver);
        m.addProperty("name", seat.name);                       // round MP2
        m.addProperty("seat", seat.seat);
        m.addProperty("left", seat.leaving);                    // it said "leave"
        m.addProperty("expired", seat.expired);                 // it never came back
        m.addProperty("grace", graceSeconds);
        m.addProperty("players", totalPlayers());               // round MP2c: 3 or more - the others play on
        tellAll(m, seat);
        BridgeGui g = seat.gui;
        javax.swing.SwingUtilities.invokeLater(() -> {
            try {
                IGameController gc = g == null ? null : g.getGameController();
                boolean out = gc instanceof forge.game.player.PlayerController pc && pc.getPlayer() != null
                        && pc.getPlayer().hasLost();              // already conceded or lost: nothing to concede or journal
                if (gc != null && !out && Main.match != null && Main.match.getGame() != null
                        && !Main.match.getGame().isGameOver()) {
                    // round RESUME1: the concede goes into the journal as if the guest had sent it - at the moment it really
                    // happens (on Swing's thread, as every journalled command), so the play-back concedes at the same point
                    // of the game (round RESUME2: its position in the journal is what does it; the "seqs" field is gone).
                    // Without it a game continued from the save still
                    // had this player in it, and the play-back stopped at the next question the original asked someone else
                    // (ORS1, seed 43063: "seat 3 never reached question 91").
                    Main.concedeWhenSafe(g, gc, System.currentTimeMillis(),
                            () -> journal(seat.seat, leftConcede()));   // see Main.concedeWhenSafe
                }
            } catch (Throwable t) {
                System.err.println("nethost: conceding for " + seat.name + " failed: " + t);
            }
        });
    }

    /** Round RESUME1: the journal line for a guest who left and was conceded for: {"c":"concede","left":true}. A play-back applies
     *  it like the guest's own Concede (Main.handle ignores "left"; an older bridge applies it too). Round RESUME2 (Karl, 8 Nov):
     *  no "seqs" any more - RESUME1's plant showed the wait on it was not what made the play-back right; the line's place in
     *  the journal (written right before gc.concede()) is. A ResumeFeeder of this version ignores "seqs" in RESUME1's saves. */
    static JsonObject leftConcede() {
        JsonObject c = new JsonObject();
        c.addProperty("c", "concede");
        c.addProperty("left", true);
        return c;
    }

    // ---- small helpers -----------------------------------------------------------------------------------------------
    static String str(JsonObject o, String key) {
        try {
            JsonElement e = o.get(key);
            return e == null || e.isJsonNull() ? "" : e.getAsString();
        } catch (Exception ex) {
            return "";
        }
    }

    static int safeInt(JsonElement e) {
        try {
            return e.getAsInt();
        } catch (Exception ex) {
            return -1;
        }
    }

    static void closeQuietly(Socket s) {
        try {
            s.close();
        } catch (IOException ignored) {
        }
    }
}
