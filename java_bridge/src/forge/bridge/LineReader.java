// SPDX-License-Identifier: GPL-3.0-or-later
package forge.bridge;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Round MP1: reads UTF-8 lines from a stream with a hard limit on the length of one line.
 *
 * BufferedReader.readLine() keeps reading until it finds a newline, however much arrives first, so a guest's PC on the internet
 * could make the host's engine hold any amount of memory by never sending one. Here a line longer than the limit is skipped up
 * to its newline (never kept) and reported as TOO_LONG, so the caller can log it and carry on. The same reader is used first
 * for the guest's hello and then, unchanged, by the guest's Wire, so nothing the guest sent after the hello is lost.
 */
final class LineReader {
    /** Returned (compare with ==) instead of a line that was longer than the limit. */
    static final String TOO_LONG = new String("<line too long>");

    private final InputStream in;
    private final int max;

    /** max <= 0: no limit (the host's own table on stdin). */
    LineReader(InputStream in, int max) {
        this.in = in instanceof BufferedInputStream ? in : new BufferedInputStream(in, 16 * 1024);
        this.max = max;
    }

    /** The same stream (and whatever it has already buffered) with another limit: the guest's hello may carry a whole deck,
     *  the game's commands may not. */
    LineReader withLimit(int newMax) {
        return new LineReader(in, newMax);
    }

    /** The next line without its line ending, TOO_LONG for an over-long one, or null at the end of the stream. */
    String readLine() throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream(256);
        boolean tooLong = false;
        int b;
        while (true) {
            b = in.read();
            if (b < 0) {
                if (buf.size() == 0 && !tooLong) {
                    return null;
                }
                break;                                      // the last line had no newline: still a line
            }
            if (b == '\n') {
                break;
            }
            if (tooLong) {
                continue;                                   // skip the rest of an over-long line, keeping nothing
            }
            if (max > 0 && buf.size() >= max) {
                tooLong = true;
                buf.reset();
                continue;
            }
            buf.write(b);
        }
        if (tooLong) {
            return TOO_LONG;
        }
        String s = buf.toString(StandardCharsets.UTF_8);
        return s.endsWith("\r") ? s.substring(0, s.length() - 1) : s;
    }

    void close() {
        try {
            in.close();
        } catch (IOException ignored) {
        }
    }
}
