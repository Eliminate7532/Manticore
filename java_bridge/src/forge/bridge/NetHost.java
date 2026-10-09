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
    static final int PROTOCOL_V = 2;                        // round MP2: rejoin, watch, hb, leave
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
            "speed");                                                                          // round PRI1

    static int blockSeconds = 60;
    static int graceSeconds = 60;                           // round MP2: how long a dropped guest's seat is held
    static int heartbeatSeconds = 5;
    static int silenceSeconds = 20;
    static int dropNoticeDelayMs = 0;                       // tests (round MP2e): a pause between a drop and its announcement
    static String hostName = "Host";
    static String code = "";
    static String guestDeckPath;
    static Deck hostDeck;
    static String password;
    static Wire hostWire;
    static volatile BridgeGui hostGui;
    static int guestsWanted = 1;                            // round MP2c: --guests N (1-3), the people who join over the network
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
                case "--parent-pid": parentPid = Long.parseLong(args[++i]); break;
                case "--block-seconds": blockSeconds = Integer.parseInt(args[++i]); break;           // tests
                case "--stall-seconds": RemoteWire.stallSeconds = Integer.parseInt(args[++i]); break; // tests
                case "--grace-seconds": graceSeconds = Integer.parseInt(args[++i]); break;           // round MP2
                case "--heartbeat-seconds": heartbeatSeconds = Integer.parseInt(args[++i]); break;   // tests
                case "--silence-seconds": silenceSeconds = Integer.parseInt(args[++i]); break;       // tests
                case "--drop-notice-delay-ms": dropNoticeDelayMs = Integer.parseInt(args[++i]); break; // tests (round MP2e)
                case "--guests": guestsWanted = Integer.parseInt(args[++i]); break;                 // round MP2c
                case "--opponent": aiDeckPaths.add(args[++i]); break;                               // round MP2c: an AI seat
                case "--journal": journalPath = args[++i]; break;                                   // round MP2b
                case "--replay": replayPath = args[++i]; break;                                     // round MP2b
                case "--resume-guest": resumeGuests.add(args[++i]); break;                          // round MP2b
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
        hostDeck = DeckSerializer.fromFile(new File(deckPath));
        if (hostDeck == null) {
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

    static void replayFinished(ResumeFeeder feeder) {
        replayDone = true;
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
        hostWire.send(m);
        BridgeGui hg = hostGui;
        javax.swing.SwingUtilities.invokeLater(() -> {
            if (hg != null) {
                hg.markDirty();
            }
            hostWire.resendPending();                        // what Forge is asking the host right now
            for (SeatWire g : guests) {
                if (g.attached()) {
                    welcomeBack(g);
                }
            }
        });
    }

    /** A guest of a resumed game, once the play-back is done: the game as it is now, and whatever it is being asked. */
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
            if (seat == null) {
                for (SeatWire g : guests) {
                    if (!g.attached() && !g.gone.get()) {
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
        taken.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(taken, cmd), NetHost::guestConnectionEnded);
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
        if (replayDone) {
            javax.swing.SwingUtilities.invokeLater(() -> welcomeBack(taken));
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
            taken.muted = true;                               // the play-back's questions are not theirs to answer
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
                everyoneBack &= g.attached();
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
        if (rejoined) {
            w.addProperty("rejoined", true);
        }
        return w;
    }

    static JsonObject ready(boolean spectator) {
        JsonObject ready = new JsonObject();
        ready.addProperty("t", "ready");
        ready.addProperty("protocol", 2);
        ready.addProperty("seats", totalPlayers());
        ready.addProperty("online", true);
        ready.addProperty("aiTimeout", Main.aiTimeout);
        ready.addProperty("loopCap", LoopGuard.cap);
        ready.addProperty("grace", graceSeconds);
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
        if (gameOver || seat.gone.get() || leaving.get()) {
            refuse(s, out, "gone", "That game has ended.");
            return;
        }
        wrong.remove(addr);
        s.setSoTimeout(0);
        out.write(welcome(seat, true) + "\n");
        out.flush();
        synchronized (seat.announce) {                          // round MP2e: after any "dropped" of the old connection
            seat.attach(s, reader.withLimit(REMOTE_LINE_MAX), cmd -> guestCommand(seat, cmd), NetHost::guestConnectionEnded);
            java.util.concurrent.ScheduledFuture<?> g = seat.graceTask;
            if (g != null) {
                g.cancel(false);
            }
            if (!started) {
                sendLobby();                                    // still waiting for the others
                return;
            }
            if (!replayDone) {                                  // round MP2b: a saved game still being played back
                seat.muted = true;
                JsonObject m = new JsonObject();
                m.addProperty("t", "lobby");
                m.addProperty("resuming", true);
                m.addProperty("wanted", guestsWanted);
                seat.sendNow(m);
                return;
            }
            seat.send(ready(false));
            System.err.println("nethost: " + seat.name + " reconnected (" + seat.pendingCount() + " question(s) asked again)");
            JsonObject back = new JsonObject();
            back.addProperty("t", "peer_back");
            back.addProperty("who", "guest");
            back.addProperty("name", seat.name);
            back.addProperty("seat", seat.seat);
            tellAll(back, seat);
        }
        BridgeGui gg = seat.gui;
        javax.swing.SwingUtilities.invokeLater(() -> {
            if (gg != null) {
                gg.markDirty();
            }
            seat.resendPending();
        });
    }

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
            if (d.getCommanders().isEmpty()) {
                return "The deck has no commander.";
            }
            if (d.getMain().countAll() < 1) {
                return "The deck has no cards besides the commander.";
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
        RegisteredPlayer p1 = RegisteredPlayer.forCommander(hostDeck);
        p1.setPlayer(new Passing.Human(hostName));          // patch 43
        BridgeGui g1 = new BridgeGui(hostWire);
        g1.events = new EventForwarder(hostWire);
        players.add(p1);
        guis.put(p1, g1);
        for (SeatWire seat : guests) {
            Deck d = DeckSerializer.fromFile(new File(seat.deckPath));
            RegisteredPlayer rp = RegisteredPlayer.forCommander(d);
            rp.setPlayer(new Passing.Human(seat.name));         // patch 43
            BridgeGui g = new BridgeGui(seat);
            g.events = new EventForwarder(seat);
            seat.gui = g;
            players.add(rp);
            guis.put(rp, g);
        }
        int i = 1;
        for (Deck d : aiDecks) {                                  // round MP2c: AI seats, named as in a game against the AIs
            RegisteredPlayer rp = RegisteredPlayer.forCommander(d);
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
        // command zone), exactly as Main does.
        Main.match.startMatch(GameType.Commander, java.util.EnumSet.of(GameType.Commander), players, guis);
        System.err.println("nethost: the game started with " + players.size() + " players (" + guests.size() + " online, "
                + aiDecks.size() + " AI)");
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
                if (gc != null && Main.match != null && Main.match.getGame() != null && !Main.match.getGame().isGameOver()) {
                    Main.concedeWhenSafe(g, gc, System.currentTimeMillis());      // see Main.concedeWhenSafe
                }
            } catch (Throwable t) {
                System.err.println("nethost: conceding for " + seat.name + " failed: " + t);
            }
        });
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
