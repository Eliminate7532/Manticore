# SPDX-License-Identifier: GPL-3.0-or-later
"""
motion.py - springs, tweens, a screen/panel shaker and a particle pool for the table's animations.

Nothing here reads a clock: every function takes `now` (seconds) or `dt` (seconds since the last frame), like forge_fx.py, so a test or a
preview render can drive time however it likes. Nothing here allocates a pygame Surface after construction (ParticlePool pre-renders its
sprites once), so it is safe to call every frame at 4K.
"""
import math
import random

import pygame


# ---- easing (t in 0..1) ------------------------------------------------------------------------------------------------------------

def clamp01(t):
    return 0.0 if t <= 0 else 1.0 if t >= 1 else t


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out_cubic(t):
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_in_cubic(t):
    t = clamp01(t)
    return t * t * t


def ease_out_quad(t):
    t = clamp01(t)
    return 1 - (1 - t) * (1 - t)


def ease_out_back(t, s=1.25):
    """Overshoots by about 5.7% (s=1.25) and settles: for things that 'land'."""
    t = clamp01(t) - 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


# ---- springs ------------------------------------------------------------------------------------------------------------------------

class Spring:
    """A number that follows `target` like a damped spring. omega = stiffness in rad/s, zeta = damping (1 = no overshoot, < 1 = some).
    Integrated with fixed sub-steps of at most 1/120 s, so it moves the same at 30 or 144 frames a second; a long pause (dt > 0.1 s,
    e.g. the window was dragged) is capped so it never explodes."""
    __slots__ = ("value", "velocity", "target", "omega", "zeta")
    MAX_STEP = 1 / 120
    MAX_DT = 0.1

    def __init__(self, value=0.0, omega=28.0, zeta=1.0):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.omega, self.zeta = omega, zeta

    def snap(self, value):
        """Jump straight to `value` and stop (animations off, or the first time a thing is seen)."""
        self.value = self.target = float(value)
        self.velocity = 0.0

    def step(self, dt):
        dt = min(max(dt, 0.0), self.MAX_DT)
        w2, d = self.omega * self.omega, 2 * self.zeta * self.omega
        while dt > 1e-9:
            h = min(dt, self.MAX_STEP)
            dt -= h
            self.velocity += (-w2 * (self.value - self.target) - d * self.velocity) * h
            self.value += self.velocity * h
        return self.value

    def settled(self, eps=0.5):
        return abs(self.value - self.target) < eps and abs(self.velocity) < eps * 10


# Tuned presets (simulated at 60 and 30 fps with this integrator; see the spec for the numbers)
HOVER = (30.0, 0.90)      # no overshoot, ~170 ms
PRESS = (45.0, 0.90)      # ~120 ms
FLIGHT = (22.0, 0.68)     # ~3.5 % overshoot, ~230 ms
SNAPBACK = (24.0, 0.80)   # ~150 ms


class Tween:
    """A fixed-length animation from `start` to `end` (for fades, ghosts, squash): value(now) and done(now)."""
    __slots__ = ("start", "end", "t0", "duration", "ease")

    def __init__(self, start, end, t0, duration, ease=ease_out_cubic):
        self.start, self.end, self.t0, self.duration, self.ease = start, end, t0, max(1e-6, duration), ease

    def progress(self, now):
        return clamp01((now - self.t0) / self.duration)

    def value(self, now):
        return lerp(self.start, self.end, self.ease(self.progress(now)))

    def done(self, now):
        return now - self.t0 >= self.duration


# ---- shake (trauma model) -----------------------------------------------------------------------------------------------------------

class Shaker:
    """Trauma-based shake: add(0..1) on an impact; offset(now) is (dx, dy) in pixels. Shake = trauma squared, so small hits barely move and
    big ones are felt; trauma drains at DECAY per second. The wobble is a sum of sines with fixed random phases (smooth, not jittery)."""
    DECAY = 1.6

    def __init__(self, max_px=10.0, seed=7):
        self.trauma, self.last, self.max_px = 0.0, None, max_px
        rnd = random.Random(seed)
        self.phases = [rnd.uniform(0, 2 * math.pi) for _ in range(4)]

    def add(self, amount, now):
        self._decay(now)
        self.trauma = min(1.0, self.trauma + amount)

    def _decay(self, now):
        if self.last is not None:
            self.trauma = max(0.0, self.trauma - self.DECAY * max(0.0, now - self.last))
        self.last = now

    def active(self, now):
        self._decay(now)
        return self.trauma > 0.001

    def offset(self, now):
        self._decay(now)
        k = self.trauma * self.trauma * self.max_px
        if k < 0.05:
            return 0, 0
        p = self.phases
        dx = k * (0.6 * math.sin(now * 47 + p[0]) + 0.4 * math.sin(now * 83 + p[1]))
        dy = k * (0.6 * math.sin(now * 53 + p[2]) + 0.4 * math.sin(now * 71 + p[3]))
        return int(round(dx)), int(round(dy))


# ---- particles ----------------------------------------------------------------------------------------------------------------------

class ParticlePool:
    """Up to `capacity` small glowing dots, all in flat lists allocated once. spawn() reuses dead slots (and, when full, overwrites the
    oldest), update(dt) moves them, draw(screen) blits one pre-rendered sprite per particle with additive blending. Colours are keys
    into `palette` ({key: (r, g, b)}); each colour gets LEVELS pre-rendered brightness steps so fading needs no per-frame work."""
    LEVELS = 6

    def __init__(self, palette, capacity=160, radius=5):
        self.capacity, self.radius = capacity, radius
        n = capacity
        self.x, self.y, self.vx, self.vy = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
        self.age, self.life, self.grav = [0.0] * n, [0.0] * n, [0.0] * n
        self.colour = [None] * n
        self.alive = [False] * n
        self.next = 0
        self.sprites = {key: [self._sprite(rgb, (lv + 1) / self.LEVELS) for lv in range(self.LEVELS)] for key, rgb in palette.items()}

    def _sprite(self, rgb, k):
        d = self.radius * 2 + 1
        s = pygame.Surface((d, d))                      # black = adds nothing under BLEND_RGB_ADD, so no alpha channel is needed
        for r in range(self.radius, 0, -1):
            f = k * (1 - r / (self.radius + 1)) ** 1.5
            pygame.draw.circle(s, (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f)), (self.radius, self.radius), r)
        return s

    def count(self):
        return sum(self.alive)

    def spawn(self, n, x, y, colour, speed=160.0, spread=math.pi * 2, angle=-math.pi / 2, life=0.6, gravity=240.0, rnd=random):
        for _ in range(n):
            i = self._free_slot()
            a = angle + rnd.uniform(-spread / 2, spread / 2)
            v = speed * rnd.uniform(0.45, 1.0)
            self.x[i], self.y[i], self.vx[i], self.vy[i] = float(x), float(y), math.cos(a) * v, math.sin(a) * v
            self.age[i], self.life[i], self.grav[i], self.colour[i], self.alive[i] = 0.0, life * rnd.uniform(0.7, 1.0), gravity, colour, True

    def _free_slot(self):
        for k in range(self.capacity):
            i = (self.next + k) % self.capacity
            if not self.alive[i]:
                self.next = (i + 1) % self.capacity
                return i
        i = self.next                                    # full: overwrite the oldest-ish slot
        self.next = (i + 1) % self.capacity
        return i

    def update(self, dt):
        dt = min(max(dt, 0.0), 0.1)
        for i in range(self.capacity):
            if not self.alive[i]:
                continue
            self.age[i] += dt
            if self.age[i] >= self.life[i]:
                self.alive[i] = False
                continue
            self.vy[i] += self.grav[i] * dt
            self.x[i] += self.vx[i] * dt
            self.y[i] += self.vy[i] * dt

    def draw(self, screen, offset=(0, 0)):
        r, ox, oy = self.radius, offset[0], offset[1]
        for i in range(self.capacity):
            if not self.alive[i]:
                continue
            left = 1 - self.age[i] / self.life[i]
            lv = min(self.LEVELS - 1, int(left * self.LEVELS))
            screen.blit(self.sprites[self.colour[i]][lv], (int(self.x[i]) - r + ox, int(self.y[i]) - r + oy), special_flags=pygame.BLEND_RGB_ADD)
