// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import com.google.gson.JsonObject;

import org.jupnp.DefaultUpnpServiceConfiguration;
import org.jupnp.UpnpService;
import org.jupnp.UpnpServiceImpl;
import org.jupnp.model.action.ActionInvocation;
import org.jupnp.model.message.UpnpResponse;
import org.jupnp.model.meta.Device;
import org.jupnp.model.meta.Service;
import org.jupnp.registry.Registry;
import org.jupnp.support.igd.PortMappingListener;
import org.jupnp.support.igd.callback.GetExternalIP;
import org.jupnp.support.model.PortMapping;
import org.jupnp.util.SpecificationViolationReporter;

import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

/**
 * Round MP1: asks the home router to forward the game's port to this PC (UPnP), the way Forge's own network host does
 * (FServerManager.mapNatPort at 3a74143), using the jUPnP library already inside forge.jar.
 *
 * It reports one line to the host's table, {"t":"upnp","ok":...,"external_ip":...,"reason":...}:
 *   ok=true                     the router opened the port; external_ip is the address the friend connects to
 *   ok=false, reason=cgnat      the router's own internet address is a private or shared one (100.64.0.0/10, 10/8, 172.16/12,
 *                               192.168/16): the internet provider (or a second router) is in the way, so no port can be
 *                               opened from here. The README's Tailscale section is the way round it.
 *   ok=false, reason=refused    a router answered but refused the mapping (text says why)
 *   ok=false, reason=no_router  no router answered within TIMEOUT_SECONDS (UPnP is off, or the network has none)
 *   ok=false, reason=error      something else went wrong (text)
 * The external address comes from the router itself (GetExternalIPAddress); no "what is my IP" website is asked.
 * stop() removes the mapping (jUPnP's PortMappingListener deletes what it added when the service shuts down).
 */
final class Upnp {
    static int timeoutSeconds = 6;
    private static volatile UpnpService service;

    private Upnp() {
    }

    static void start(int port, Consumer<JsonObject> report) {
        Thread t = new Thread(() -> run(port, report), "upnp");
        t.setDaemon(true);
        t.start();
    }

    private static void run(int port, Consumer<JsonObject> report) {
        AtomicBoolean done = new AtomicBoolean(false);
        AtomicReference<String> failure = new AtomicReference<>("");
        try {
            String local = localAddress();
            if (local == null) {
                report.accept(result(false, null, "no_network", "This PC has no network address."));
                return;
            }
            PortMapping pm = new PortMapping(port, local, PortMapping.Protocol.TCP, "Manticore");
            SpecificationViolationReporter.disableReporting();      // routers often bend the UPnP rules; don't log each one
            UpnpService s = new UpnpServiceImpl(new DefaultUpnpServiceConfiguration());
            service = s;
            s.startup();
            s.getRegistry().addListener(new PortMappingListener(pm) {
                @Override
                public synchronized void deviceAdded(Registry registry, Device device) {
                    if (done.get()) {
                        return;
                    }
                    Service<?, ?> cs = discoverConnectionService(device);
                    if (cs == null) {
                        return;                                      // not a router (a TV, a printer, ...)
                    }
                    super.deviceAdded(registry, device);             // adds the mapping, synchronously
                    List<PortMapping> active = activePortMappings.get(cs);
                    boolean mapped = active != null && !active.isEmpty();
                    String ext = externalIp(s, cs);
                    if (!done.compareAndSet(false, true)) {
                        return;
                    }
                    if (ext != null && behindAnotherNat(ext)) {
                        report.accept(result(false, ext, "cgnat", "The router's own internet address is " + ext + "."));
                    } else if (mapped) {
                        report.accept(result(true, ext, null, null));
                    } else {
                        report.accept(result(false, ext, "refused", failure.get()));
                    }
                }

                @Override
                protected void handleFailureMessage(String message) {
                    super.handleFailureMessage(message);
                    failure.set((failure.get() + " " + message).trim());
                }
            });
            s.getControlPoint().search();
            long end = System.currentTimeMillis() + timeoutSeconds * 1000L;
            while (!done.get() && System.currentTimeMillis() < end) {
                Thread.sleep(100);
            }
            if (done.compareAndSet(false, true)) {
                report.accept(result(false, null, "no_router", "No router answered within " + timeoutSeconds + " seconds."));
            }
        } catch (Throwable e) {
            System.err.println("nethost: UPnP failed: " + e);
            if (done.compareAndSet(false, true)) {
                report.accept(result(false, null, "error", String.valueOf(e.getMessage())));
            }
        }
    }

    private static String externalIp(UpnpService s, Service<?, ?> cs) {
        AtomicReference<String> ip = new AtomicReference<>();
        try {
            s.getControlPoint().execute(new GetExternalIP(cs) {
                @Override
                protected void success(String externalIPAddress) {
                    ip.set(externalIPAddress);
                }

                @Override
                public void failure(ActionInvocation invocation, UpnpResponse operation, String defaultMsg) {
                    System.err.println("nethost: the router did not say its external address: " + defaultMsg);
                }
            }).get(5, TimeUnit.SECONDS);
        } catch (Exception e) {
            System.err.println("nethost: the router did not say its external address: " + e);
        }
        String v = ip.get();
        return v == null || v.isBlank() ? null : v.trim();
    }

    static JsonObject result(boolean ok, String externalIp, String reason, String text) {
        JsonObject m = new JsonObject();
        m.addProperty("t", "upnp");
        m.addProperty("ok", ok);
        if (externalIp != null) m.addProperty("external_ip", externalIp);
        if (reason != null) m.addProperty("reason", reason);
        if (text != null && !text.isBlank()) m.addProperty("text", text.length() > 300 ? text.substring(0, 300) : text);
        return m;
    }

    /** True for an address no one on the internet can connect to: carrier-grade NAT (100.64.0.0/10), the private ranges,
     *  link-local and loopback. A router reporting one of these as its internet address sits behind another NAT. */
    static boolean behindAnotherNat(String ip) {
        String[] p = ip.split("\\.");
        if (p.length != 4) {
            return false;
        }
        int a, b;
        try {
            a = Integer.parseInt(p[0]);
            b = Integer.parseInt(p[1]);
        } catch (NumberFormatException e) {
            return false;
        }
        return a == 10 || a == 127 || (a == 100 && b >= 64 && b <= 127) || (a == 172 && b >= 16 && b <= 31)
                || (a == 192 && b == 168) || (a == 169 && b == 254) || a == 0;
    }

    /** This PC's address on the home network: the one used to reach the internet (a UDP "connect" sends nothing), else the
     *  first private IPv4 address of a running network adapter. */
    static String localAddress() {
        try (DatagramSocket probe = new DatagramSocket()) {
            probe.connect(InetAddress.getByName("192.0.2.1"), 9);         // TEST-NET-1: never reached, only routed
            InetAddress a = probe.getLocalAddress();
            if (a instanceof Inet4Address && !a.isAnyLocalAddress() && !a.isLoopbackAddress()) {
                return a.getHostAddress();
            }
        } catch (Exception ignored) {
        }
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback() || ni.isVirtual()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                        return a.getHostAddress();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    /** Removes the mapping (the listener deletes what it added during shutdown). Safe to call more than once. */
    static void stop() {
        UpnpService s = service;
        service = null;
        if (s == null) {
            return;
        }
        try {
            s.shutdown();
        } catch (Throwable e) {                       // the JDK wraps a failed multicast leave in AssertionError (Forge saw it too)
            System.err.println("nethost: UPnP shutdown incomplete: " + e);
        }
    }
}
