# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ENG-BRAWL: the Brawl stall - Forge's game thread died in AbstractGuiGame.awaitNextInput.

Forge 9a6f776's awaitNextInput (game thread) writes a new TimerTask into a field and reads the field again to schedule it, with no
lock; Swing's thread clears that field (cancelAwaitNextInput, and an earlier task's own runnable) under synchronized (the timer).
A clear in between gave Timer.schedule a null task: NullPointerException, the game thread died, the game froze (Brawl soak of
27 Oct, game 26). BridgeGui.awaitNextInput now runs Forge's method holding that same monitor.

The probe drives the real BridgeGui (from java_bridge/forge_bridge.jar) on three threads: two calling awaitNextInput, as Forge's
game thread and InputBase.setFinished on Swing's thread do, one calling cancelAwaitNextInput, as InputBase.showMessageInitial does.
On main's jar (0.28.53.14) it counted 1,363-1,739 NullPointerExceptions and 185,796-272,221 IllegalStateExceptions in 2 s (3 runs).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import forge_client as fc

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_JAR = os.path.join(BASE, "java_bridge", "forge_bridge.jar")
FORGE_JAR = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
LANGUAGES = os.path.join(fc.DEFAULT_RUNTIME, "res", "languages")
BRIDGE_GUI = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "BridgeGui.java")
PROBE_MILLIS = 2000


def _jdk_tool(name):
    java = fc.find_java()
    for cand in ([os.path.join(os.path.dirname(java), n) for n in (name, name + ".exe")] if java else []):
        if os.path.isfile(cand):
            return cand
    return shutil.which(name)


PROBE = r"""package forge.bridge;

import java.io.OutputStream;
import java.io.PrintStream;
import java.lang.reflect.Field;
import java.util.Timer;
import java.util.TimerTask;
import java.util.concurrent.atomic.AtomicLong;

/** Round ENG-BRAWL: Forge's AbstractGuiGame.awaitNextInput (game thread) against cancelAwaitNextInput (Swing's thread). */
public class AwaitRaceProbe {
    /** Never runs a task: the probe is about scheduling, and a task that ran would need Forge's whole GUI. */
    static final class ParkingTimer extends Timer {
        ParkingTimer() { super("probe", true); }
        @Override public void schedule(TimerTask task, long delay) { super.schedule(task, 3_600_000L); }
    }

    public static void main(String[] a) throws Exception {
        long millis = Long.parseLong(a[0]);
        forge.util.Localizer.getInstance().initialize("en-US", a[1]);
        BridgeGui gui = new BridgeGui(new Wire(new PrintStream(OutputStream.nullOutputStream())));
        Field f = forge.gamemodes.match.AbstractGuiGame.class.getDeclaredField("awaitNextInputTimer");
        f.setAccessible(true);
        ParkingTimer timer = new ParkingTimer();
        f.set(gui, timer);
        AtomicLong awaits = new AtomicLong(), cancels = new AtomicLong(), npe = new AtomicLong(), ise = new AtomicLong(),
                other = new AtomicLong();
        long end = System.nanoTime() + millis * 1_000_000L;
        Runnable game = () -> {
            while (System.nanoTime() < end) {
                try {
                    gui.awaitNextInput();
                } catch (NullPointerException e) {
                    npe.incrementAndGet();
                } catch (IllegalStateException e) {
                    ise.incrementAndGet();
                } catch (RuntimeException e) {
                    other.incrementAndGet();
                }
                awaits.incrementAndGet();
            }
        };
        Runnable edt = () -> {
            while (System.nanoTime() < end) {
                gui.cancelAwaitNextInput();
                cancels.incrementAndGet();
            }
        };
        Thread[] ts = {new Thread(game, "Game-0"), new Thread(edt, "AWT-EventQueue-0"), new Thread(game, "Game-1")};
        for (Thread t : ts) t.start();
        for (Thread t : ts) t.join();
        timer.cancel();
        System.out.println("awaits=" + awaits.get());
        System.out.println("cancels=" + cancels.get());
        System.out.println("npe=" + npe.get());
        System.out.println("ise=" + ise.get());
        System.out.println("other=" + other.get());
    }
}
"""


def run_probe(bridge_jar, millis=PROBE_MILLIS):
    """Compiles and runs the probe against bridge_jar; returns (counts, stderr)."""
    tmp = tempfile.mkdtemp(prefix="eng_brawl_")
    try:
        src = os.path.join(tmp, "forge", "bridge")
        os.makedirs(src)
        with open(os.path.join(src, "AwaitRaceProbe.java"), "w", encoding="utf-8") as f:
            f.write(PROBE)
        cp = os.pathsep.join([bridge_jar, FORGE_JAR])
        r = subprocess.run([_jdk_tool("javac"), "-nowarn", "-cp", cp, "-d", tmp, os.path.join(src, "AwaitRaceProbe.java")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        env = dict(os.environ)
        env.pop("JAVA_TOOL_OPTIONS", None)
        r = subprocess.run([fc.find_java(), "-cp", tmp + os.pathsep + cp, "forge.bridge.AwaitRaceProbe", str(millis),
                            LANGUAGES + os.sep], capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stderr
        counts = {k: int(v) for k, v in (ln.split("=", 1) for ln in r.stdout.splitlines() if re.match(r"^[a-z]+=\d+$", ln))}
        return counts, r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(_jdk_tool("javac") and os.path.isfile(FORGE_JAR), "needs a JDK (javac) and forge_runtime/")
class AwaitRaceTests(unittest.TestCase):

    def test_await_and_cancel_on_different_threads_never_break_the_game_thread(self):
        counts, err = run_probe(BRIDGE_JAR)
        self.assertGreater(counts["awaits"], 100000, counts)        # the race had its chances (main's jar: ~2.7 million)
        self.assertGreater(counts["cancels"], 100000, counts)
        self.assertEqual(counts["npe"], 0, counts)                  # the Brawl stall's exception
        self.assertEqual(counts["ise"], 0, counts)                  # one task scheduled twice
        self.assertEqual(counts["other"], 0, counts)
        self.assertNotIn("lost a race", err)                        # the lock prevented it - the fallback catch never ran


@unittest.skipUnless(_jdk_tool("javap") and os.path.isfile(FORGE_JAR), "needs a JDK (javap) and forge_runtime/")
class ForgeShapeTests(unittest.TestCase):
    """The fix leans on Forge's private field and on which methods are final; a Forge update (ROADMAP Phase F) must keep them."""

    def test_forge_still_has_the_field_and_the_methods_the_guard_relies_on(self):
        env = dict(os.environ)
        env.pop("JAVA_TOOL_OPTIONS", None)
        r = subprocess.run([_jdk_tool("javap"), "-p", "-cp", FORGE_JAR, "forge.gamemodes.match.AbstractGuiGame"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("private java.util.Timer awaitNextInputTimer;", out)
        self.assertIn("private java.util.TimerTask awaitNextInputTask;", out)
        self.assertIn("public void awaitNextInput();", out)             # not final: BridgeGui overrides it
        self.assertIn("public final void cancelAwaitNextInput();", out)


class SourceTests(unittest.TestCase):

    def test_bridge_gui_runs_forges_await_under_the_timers_monitor(self):
        with open(BRIDGE_GUI, encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("public void awaitNextInput()"):]
        body = body[:body.index("private void awaitNextInputGuarded()")]
        self.assertIn("synchronized (timer)", body)
        self.assertIn('getDeclaredField("awaitNextInputTimer")', src)


if __name__ == "__main__":
    unittest.main()
