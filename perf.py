# SPDX-License-Identifier: GPL-3.0-or-later
"""
perf.py - how fast the table really runs on this computer, and when it needs to redraw at all.

  * FrameStats: the last N frame times (ms), split by what was on screen ("table", "dialog"); p50 / p95 / max; engine response times
    (a command sent -> the next snapshot). Shown by the F3 overlay, written as one summary line to perf_log.txt when the program closes,
    and put into bug reports.
  * FramePacer: decides each loop whether to draw. Input or a new snapshot -> draw now. Something moving (a card flying, a spring, a
    shake, particles) -> 60 fps. Only slow pulses (a glowing button, marching dashes) -> 30 fps. Nothing -> a floor of 4 fps, so a toast
    or a fading line can never get stuck on screen even if something forgot to say it was animating.
No pygame. The clock is passed in.
"""
import math
from collections import deque

FLOOR_FPS = 4
PULSE_FPS = 30


def percentile(values, p):
    """Nearest-rank percentile (p50 of 1..100 is 50, p95 is 95)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, math.ceil(p / 100.0 * len(s)) - 1)
    return s[min(len(s) - 1, k)]


class FrameStats:
    def __init__(self, size=600):
        self.frames = {}                      # scene -> deque of ms
        self.size = size
        self.engine = deque(maxlen=200)       # ms from a command to the next snapshot
        self._sent_at = None
        self.drawn = self.skipped = 0

    def frame(self, ms, scene="table"):
        self.frames.setdefault(scene, deque(maxlen=self.size)).append(ms)
        self.drawn += 1

    def skip(self):
        self.skipped += 1

    def command_sent(self, now):
        if self._sent_at is None:
            self._sent_at = now

    def snapshot_arrived(self, now):
        if self._sent_at is not None:
            self.engine.append((now - self._sent_at) * 1000)
            self._sent_at = None

    def summary(self, scene="table"):
        v = list(self.frames.get(scene, ()))
        return {"n": len(v), "p50": round(percentile(v, 50), 1), "p95": round(percentile(v, 95), 1), "max": round(max(v), 1) if v else 0.0}

    def engine_summary(self):
        v = list(self.engine)
        return {"n": len(v), "p50": round(percentile(v, 50)), "p95": round(percentile(v, 95))}

    def line(self):
        """One line for perf_log.txt / a bug report."""
        parts = [f"{scene}: p50 {s['p50']} ms p95 {s['p95']} ms max {s['max']} ms (n={s['n']})"
                 for scene, s in ((sc, self.summary(sc)) for sc in sorted(self.frames))]
        e = self.engine_summary()
        parts.append(f"engine reply p50 {e['p50']} ms p95 {e['p95']} ms (n={e['n']})")
        total = self.drawn + self.skipped
        parts.append(f"frames drawn {self.drawn} of {total}")
        return "; ".join(parts)


class FramePacer:
    def __init__(self):
        self.last_draw = -1e9
        self.moving_until = 0.0              # something said "I am moving" (a spring, a flight, a shake, particles)
        self.pulsing = False                 # something with a slow pulse was on screen in the last frame

    def moving(self, now, seconds=0.05):
        self.moving_until = max(self.moving_until, now + seconds)

    def should_draw(self, now, dirty):
        """dirty: input arrived, a snapshot / event / request arrived, the window changed, or a dialog opened or closed."""
        gap = now - self.last_draw
        if dirty or now < self.moving_until:
            return True
        if self.pulsing and gap >= 1.0 / PULSE_FPS - 0.002:
            return True
        return gap >= 1.0 / FLOOR_FPS

    def drew(self, now, pulsing):
        self.last_draw = now
        self.pulsing = pulsing
