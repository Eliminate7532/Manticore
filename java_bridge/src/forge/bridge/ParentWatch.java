package forge.bridge;

/**
 * Round 29a: the bridge exits as soon as the program that started it is gone.
 *
 * The bridge already exits when its input closes (Wire.readLoop -> System.exit), but it only starts reading its input once
 * Forge has finished starting (FModel.initialize, the card scripts, the match): 10-20 seconds on Karl's PC. A program killed
 * in that window (Task Manager, a crash, a closed console) left java.exe running until Forge got there. Measured in the
 * cloud sandbox with a frozen build: killed 40 s in, Java was gone within 5 s; killed 4 s in, Java was still running 5 s
 * later and exited between 5 and 10 s. Alpha must-have 1 asks for "no java.exe left behind, within about 5 seconds".
 *
 * forge_client passes --parent-pid (its own process id); a daemon thread checks it every second from the very start of
 * main(), before Forge is initialised, and exits the moment that process is no longer alive. ProcessHandle works on
 * Windows, Linux and macOS (Java 9+). Without --parent-pid (Forge started by hand, older Python) nothing changes.
 */
final class ParentWatch {
    static final long INTERVAL_MS = 1000;

    private ParentWatch() { }

    static void start(long pid) {
        Thread t = new Thread(() -> {
            while (true) {
                if (!alive(pid)) {
                    System.err.println("bridge: the program that started me (process " + pid + ") is gone; exiting");
                    System.err.flush();
                    Runtime.getRuntime().halt(0);     // halt, not exit: never wait on Forge's shutdown hooks or a busy game thread
                }
                try {
                    Thread.sleep(INTERVAL_MS);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }, "bridge parent watch");
        t.setDaemon(true);
        t.start();
    }

    static boolean alive(long pid) {
        return ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false);
    }
}
