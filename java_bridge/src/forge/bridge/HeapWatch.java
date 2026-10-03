package forge.bridge;

/**
 * Round 28d: the most memory Java's heap has held during this game, sampled every 2 seconds on a daemon thread and sent with
 * "game_over" (peakHeapMb, maxHeapMb). The soak summary shows the largest per number of players, so Java's -Xmx (3 GB, a
 * guess until now) can be chosen from real games and a minimum PC stated for the alpha (ALPHA_FINISH_LINE must-have 14).
 */
final class HeapWatch {
    private static volatile long peakBytes = 0;      // heap in use, garbage included (an upper bound; the JVM collects lazily)
    private static volatile long peakLiveBytes = 0;  // heap still in use just after a collection: what the game really needs

    private HeapWatch() {}

    static void start() {
        Thread t = new Thread(() -> {
            while (true) {
                sample();
                try {
                    Thread.sleep(2000);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }, "bridge heap watch");
        t.setDaemon(true);
        t.start();
    }

    static void sample() {
        Runtime r = Runtime.getRuntime();
        long used = r.totalMemory() - r.freeMemory();
        if (used > peakBytes) peakBytes = used;
        long live = 0;
        for (java.lang.management.MemoryPoolMXBean pool : java.lang.management.ManagementFactory.getMemoryPoolMXBeans()) {
            if (pool.getType() != java.lang.management.MemoryType.HEAP) continue;
            java.lang.management.MemoryUsage after = pool.getCollectionUsage();
            if (after != null) live += after.getUsed();
        }
        if (live > peakLiveBytes) peakLiveBytes = live;
    }

    static long peakLiveMb() {
        sample();
        return peakLiveBytes / (1024 * 1024);
    }

    static long peakMb() {
        sample();
        return peakBytes / (1024 * 1024);
    }

    static long maxMb() {
        return Runtime.getRuntime().maxMemory() / (1024 * 1024);
    }
}
