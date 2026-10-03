# SPDX-License-Identifier: GPL-3.0-or-later
"""
audio.py - sound for the table: a small "director" over pygame.mixer.

  * Cues are named ("card.play", "damage.player" ...) and described in sounds/cues.json (bus, loudness, variation, cooldown, voice limit,
    ducking). A cue whose sound file is missing is SYNTHESISED here from a tiny recipe, so the game has sound before any asset is chosen.
  * Buses: ui, sfx, stinger (each its own reserved mixer channels) and music (pygame.mixer.music). Volume = master x bus x cue.
  * pygame.mixer cannot change pitch while playing, so variety is made when a cue loads: `variants` resampled copies spread over
    +/- `semitones`, played in shuffled order without an immediate repeat, plus a little random gain.
  * Ducking: a cue with "duck" lowers the named buses for a moment (attack / hold / release), worked out in update(now).
  * No sound device, or sound turned off: every call is a no-op. Time is passed in (`now`), never read here.
  * Round AU1: real recordings (sounds/sfx, built by tools/build_sounds.py from CC0 sources). Two players for long sounds, never
    decoded up front ("stream" cues): an AMBIENCE loop per table (two reserved channels, cross-faded; the file is decoded on a
    background thread so a change of table never stalls a frame) and MUSIC through pygame.mixer.music (streamed from disk): the
    title track looping, or in a game a shuffled playlist with a quiet gap between tracks, and a one-off sting at the end.
"""
import array
import json
import math
import os
import random
import threading

import pygame

SAMPLE_RATE = 44100
BUFFER = 512                                   # samples: about 11.6 ms of extra delay; smaller crackles on some Windows drivers
BUS_CHANNELS = {"ui": 4, "sfx": 14, "stinger": 2, "ambience": 2}
DUCK_ATTACK, DUCK_HOLD, DUCK_RELEASE = 0.06, 0.12, 0.35
DEFAULT_VOLUMES = {"master": 0.8, "ui": 0.6, "sfx": 0.8, "stinger": 0.8, "music": 0.45, "ambience": 0.6}
AMBIENCE_FADE = 2.5                            # seconds to cross-fade one table's ambience into the next
AMBIENCE_KEEP = 2                              # decoded ambience loops kept in memory (about 10 MB each)
MUSIC_FADE = 2.0                               # seconds to fade the music out when it changes
MUSIC_GAP = (25.0, 80.0)                       # silence between two tracks in a game, seconds (Diablo let the room breathe)


def db(x):
    return 10 ** (x / 20.0)


# ---- synthesis (placeholders until real CC0 sounds are chosen) ------------------------------------------------------------------------

def _render(seconds, fn, gain=0.7, rnd=None):
    rnd = rnd or random.Random(1)
    n = int(SAMPLE_RATE * seconds)
    buf = array.array("h")
    for i in range(n):
        v = max(-1.0, min(1.0, fn(i / SAMPLE_RATE, rnd) * gain))
        s = int(v * 32000)
        buf.append(s)
        buf.append(s)
    return buf


_SYNTH_CACHE = {}          # recipe fingerprint -> array('h'): the recipes are a small fixed set (cues.json), so this is unbounded
                            # on purpose -- it just means each distinct placeholder sound is only ever synthesised once per process,
                            # instead of once per AudioDirector (several tables in the same test run, say a "New game" that rebuilds
                            # one, would otherwise redo the same trig loops every time)


def synth(recipe):
    """int16 stereo samples for one of the placeholder recipes. Cached by the recipe's own values, since building one is the slow
    part of starting the director (about 30-40 ms each; ~1.3 s for every cue the first time)."""
    fingerprint = json.dumps(recipe, sort_keys=True)
    cached = _SYNTH_CACHE.get(fingerprint)
    if cached is not None:
        return array.array("h", cached)
    result = _synth_uncached(recipe)
    _SYNTH_CACHE[fingerprint] = result
    return array.array("h", result)


def _synth_uncached(recipe):
    kind, p = recipe.get("type"), recipe
    if kind == "tick":                                   # hover / tap: a tiny filtered click
        f = p.get("freq", 3200)
        return _render(0.035, lambda t, r: math.sin(2 * math.pi * f * t) * math.exp(-t * 180), p.get("gain", 0.5))
    if kind == "thud":                                   # card lands / impact: falling sine + a breath of noise
        f0, f1 = p.get("f0", 140), p.get("f1", 55)
        return _render(p.get("len", 0.22), lambda t, r: (0.8 * math.sin(2 * math.pi * (f0 + (f1 - f0) * min(1, t * 6)) * t)
                                                          + 0.25 * (r.random() * 2 - 1) * math.exp(-t * 70)) * math.exp(-t * 16), p.get("gain", 0.8))
    if kind == "whoosh":                                 # card thrown: noise that swells and fades
        ln = p.get("len", 0.2)
        state = {"y": 0.0}

        def f(t, r):
            a = 0.15 + 0.5 * (t / ln)                    # one-pole low-pass opening up = a rising swish
            state["y"] += a * ((r.random() * 2 - 1) - state["y"])
            return state["y"] * math.sin(math.pi * min(1.0, t / ln))
        return _render(ln, f, p.get("gain", 0.6))
    if kind == "chime":                                  # life gain / spell cast: a few harmonics
        f = p.get("freq", 880)
        return _render(p.get("len", 0.45), lambda t, r: sum(math.sin(2 * math.pi * f * k * t) / k for k in (1, 2, 3)) * 0.45 * math.exp(-t * 7),
                       p.get("gain", 0.5))
    if kind == "gong":                                   # turn stinger / game end: slow inharmonic bell
        f = p.get("freq", 180)
        return _render(p.get("len", 1.2), lambda t, r: (math.sin(2 * math.pi * f * t) + 0.5 * math.sin(2 * math.pi * f * 2.76 * t)
                                                         + 0.25 * math.sin(2 * math.pi * f * 5.4 * t)) * 0.45 * math.exp(-t * 3), p.get("gain", 0.6))
    if kind == "riffle":                                 # shuffle: a burst of little noise ticks
        return _render(0.5, lambda t, r: (r.random() * 2 - 1) * (0.5 + 0.5 * math.sin(2 * math.pi * 38 * t)) * math.exp(-t * 3), p.get("gain", 0.35))
    raise ValueError(f"unknown synth recipe {kind!r}")


def repitch(samples, semitones):
    """A copy of int16 stereo `samples` played `semitones` higher (shorter) or lower (longer): linear resampling, no numpy."""
    ratio = 2 ** (semitones / 12.0)
    frames = len(samples) // 2
    n = max(1, int(frames / ratio))
    out = array.array("h", bytes(4 * n))
    for i in range(n):
        pos = i * ratio
        j = int(pos)
        k = min(j + 1, frames - 1)
        f = pos - j
        out[2 * i] = int(samples[2 * j] * (1 - f) + samples[2 * k] * f)
        out[2 * i + 1] = int(samples[2 * j + 1] * (1 - f) + samples[2 * k + 1] * f)
    return out


# ---- the director ---------------------------------------------------------------------------------------------------------------------

class Cue:
    def __init__(self, cid, spec):
        self.id = cid
        self.bus = spec.get("bus", "sfx")
        self.gain = db(spec.get("gain_db", 0.0))
        self.cooldown = spec.get("cooldown_ms", 0) / 1000.0
        self.max_voices = spec.get("max_voices", 4)
        self.duck = spec.get("duck") or {}              # {"music": -6, "sfx": -3}
        self.variants = []                              # pygame Sounds
        self.last_play = -1e9
        self.last_variant = -1
        self.spec = spec


class AudioDirector:
    def __init__(self, manifest_path, base_dir, volumes=None, enabled=True, rnd=None):
        self.enabled, self.ready = enabled, False
        self.volumes = dict(DEFAULT_VOLUMES, **(volumes or {}))
        self.rnd = rnd or random.Random()
        self.cues, self.channels, self.voices = {}, {}, []           # voices: [(channel, cue id, bus, start time)]
        self.ducks = []                                              # [(bus, gain_db, start time)]
        self.error = None
        # round AU1: the long sounds
        self.streams_on = {"music": True, "ambience": True}       # the player's switches (Cog > Music / Ambience)
        self.muted = False                                         # Sound off (M): streams fall silent too
        self.amb_want = None                                       # cue id the table wants, or None
        self.amb_now = None                                        # (cue id, channel, start time) playing / fading in
        self.amb_out = []                                          # [(channel, start time, cue id)] fading out
        self.amb_cache = {}                                        # cue id -> pygame Sound, most recent last
        self.amb_loading = set()
        self._amb_lock = threading.Lock()
        self.music_mode = None                                     # None / "title" / "game"
        self.music_cue = None                                      # the track playing now
        self.music_next_at = None                                  # in a game: when the next track starts (after the gap)
        self.music_sting = None                                    # (cue id, at time): a one-off to start at `at`
        self.music_order = []
        if not enabled:
            return
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(SAMPLE_RATE, -16, 2, BUFFER)
            total = sum(BUS_CHANNELS.values())
            pygame.mixer.set_num_channels(total + 4)
            pygame.mixer.set_reserved(total)                         # Sound.play() without a channel never steals ours
            i = 0
            for bus, n in BUS_CHANNELS.items():
                self.channels[bus] = [pygame.mixer.Channel(i + k) for k in range(n)]
                i += n
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for cid, spec in manifest["cues"].items():
                self.cues[cid] = self._load(cid, spec, base_dir)
            self.ready = True
        except (pygame.error, OSError, ValueError, KeyError) as e:     # no sound device, broken manifest: play silently
            self.error = str(e)
            self.ready = False

    def _load(self, cid, spec, base_dir):
        cue = Cue(cid, spec)
        if spec.get("stream"):                                       # ambience / music: played by their own players, from disk
            cue.paths = [os.path.join(base_dir, n) for n in spec.get("files", []) if os.path.isfile(os.path.join(base_dir, n))]
            return cue
        sources = []
        for name in spec.get("files", []):
            path = os.path.join(base_dir, name)
            if os.path.isfile(path):
                snd = pygame.mixer.Sound(path)
                sources.append(array.array("h", snd.get_raw()))
        if not sources and spec.get("synth"):
            sources.append(synth(spec["synth"]))
        n, spread = spec.get("variants", 1), spec.get("semitones", 0.0)
        for src in sources:
            if n <= 1 or not spread:
                cue.variants.append(pygame.mixer.Sound(buffer=src.tobytes()))
                continue
            for k in range(n):
                st = -spread + 2 * spread * k / (n - 1)
                cue.variants.append(pygame.mixer.Sound(buffer=repitch(src, st).tobytes()))
        return cue

    # -- playing --

    def bus_gain(self, bus, now):
        g = self.volumes.get("master", 1.0) * self.volumes.get(bus, 1.0)
        for dbus, gdb, t0 in self.ducks:
            if dbus != bus:
                continue
            age = now - t0
            if age < DUCK_ATTACK:
                k = age / DUCK_ATTACK
            elif age < DUCK_ATTACK + DUCK_HOLD:
                k = 1.0
            else:
                k = max(0.0, 1 - (age - DUCK_ATTACK - DUCK_HOLD) / DUCK_RELEASE)
            g *= db(gdb * k)
        return g

    def play(self, cid, now, pan=0.0, gain_db=0.0):
        """Play a cue; pan -1 (left) .. +1 (right). Returns True when it sounded (False: off, unknown, cooling down or at its voice limit)."""
        if not self.ready:
            return False
        cue = self.cues.get(cid)
        if cue is None or not cue.variants:
            return False
        if now - cue.last_play < cue.cooldown:
            return False
        self._reap()
        if sum(1 for v in self.voices if v[1] == cid) >= cue.max_voices:
            return False
        ch = self._channel(cue.bus, now)
        if ch is None:
            return False
        k = self.rnd.randrange(len(cue.variants))
        if len(cue.variants) > 1 and k == cue.last_variant:
            k = (k + 1) % len(cue.variants)
        cue.last_variant, cue.last_play = k, now
        jitter = self.rnd.uniform(-1.5, 1.5) if len(cue.variants) > 1 else 0.0
        base = cue.gain * db(gain_db + jitter)
        pan = max(-0.7, min(0.7, pan))                               # gentle: a card at the screen edge is not one-eared
        left = base * 1.414 * math.cos((pan + 1) * math.pi / 4)      # equal-power pan; 1.414 keeps the centre at full level
        right = base * 1.414 * math.sin((pan + 1) * math.pi / 4)
        for bus, gdb in cue.duck.items():                            # a big hit lowers the other buses, not itself
            self.ducks.append((bus, gdb, now))
        g = self.bus_gain(cue.bus, now)
        ch.play(cue.variants[k])
        ch.set_volume(min(1.0, left * g), min(1.0, right * g))
        self.voices.append((ch, cid, cue.bus, now, left, right))
        return True

    def _reap(self):
        self.voices = [v for v in self.voices if v[0].get_busy()]

    def _channel(self, bus, now):
        chans = self.channels.get(bus) or []
        for ch in chans:
            if not ch.get_busy():
                return ch
        busy = [v for v in self.voices if v[2] == bus]
        if not busy:
            return None
        oldest = min(busy, key=lambda v: v[3])                        # all busy: steal the oldest voice on this bus
        oldest[0].stop()
        self.voices.remove(oldest)
        return oldest[0]

    def update(self, now):
        """Once a frame: apply ducking to what is playing, and forget finished ducks."""
        if not self.ready:
            return
        self.ducks = [d for d in self.ducks if now - d[2] < DUCK_ATTACK + DUCK_HOLD + DUCK_RELEASE]
        self._reap()
        for ch, _cid, bus, _t0, left, right in self.voices:
            g = self.bus_gain(bus, now)
            ch.set_volume(min(1.0, left * g), min(1.0, right * g))
        self._update_ambience(now)
        self._update_music(now)

    def set_volume(self, bus, value):
        self.volumes[bus] = max(0.0, min(1.0, value))

    def stop_all(self):
        if self.ready:
            pygame.mixer.stop()

    def stop_bus(self, bus):
        """Stop what one bus is playing (the end of a game cuts the table's sounds, never the bell that says who won)."""
        if not self.ready:
            return
        for v in [v for v in self.voices if v[2] == bus]:
            v[0].stop()
            self.voices.remove(v)

    # -- the long sounds (round AU1) --

    def stream_gain(self, bus, now):
        if self.muted or not self.streams_on.get(bus, True):
            return 0.0
        return self.bus_gain(bus, now)

    def set_ambience(self, cid):
        """The loop this screen should have (None for silence). Cross-fades when it changes; safe to call every frame."""
        if cid == self.amb_want:
            return
        self.amb_want = cid
        if self.ready and cid and cid not in self.amb_cache:
            self._load_ambience(cid)

    def _load_ambience(self, cid):
        cue = self.cues.get(cid)
        paths = getattr(cue, "paths", None)
        if not paths or cid in self.amb_loading:
            return
        self.amb_loading.add(cid)

        def work():
            try:
                snd = pygame.mixer.Sound(paths[0])
            except (pygame.error, OSError):
                snd = None
            with self._amb_lock:
                self.amb_loading.discard(cid)
                if snd is not None:
                    self.amb_cache[cid] = snd
                    while len(self.amb_cache) > AMBIENCE_KEEP:
                        oldest = next(k for k in self.amb_cache if k != cid)
                        if self.amb_now and self.amb_now[0] == oldest:
                            break
                        del self.amb_cache[oldest]
        threading.Thread(target=work, daemon=True, name="ambience-load").start()

    def _update_ambience(self, now):
        playing = self.amb_now[0] if self.amb_now else None
        if self.amb_want != playing:
            with self._amb_lock:
                snd = self.amb_cache.get(self.amb_want) if self.amb_want else None
            if self.amb_want is None or snd is not None:
                if self.amb_now:
                    self.amb_out.append((self.amb_now[1], now, self.amb_now[0]))
                    self.amb_now = None
                if snd is not None:
                    chans = self.channels.get("ambience") or []
                    busy = {c for c, _t, _c in self.amb_out}
                    ch = next((c for c in chans if c not in busy), chans[0] if chans else None)
                    if ch is not None:
                        ch.stop()
                        self.amb_out = [o for o in self.amb_out if o[0] is not ch]
                        ch.set_volume(0.0)
                        ch.play(snd, loops=-1)
                        self.amb_now = (self.amb_want, ch, now)
        g = self.stream_gain("ambience", now)
        if self.amb_now:
            cid, ch, t0 = self.amb_now
            k = min(1.0, (now - t0) / AMBIENCE_FADE)
            ch.set_volume(g * self.cues[cid].gain * k)
        keep = []
        for ch, t0, cid in self.amb_out:
            k = 1.0 - (now - t0) / AMBIENCE_FADE
            if k <= 0:
                ch.stop()
            else:
                ch.set_volume(g * self.cues[cid].gain * k)
                keep.append((ch, t0, cid))
        self.amb_out = keep

    def set_music(self, mode, now):
        """'title' (its track, looping), 'game' (the game tracks, shuffled, with a gap between), or None. Safe every frame."""
        if mode == self.music_mode:
            return
        self.music_mode = mode
        self.music_next_at = None
        if not self.ready:
            return
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.fadeout(int(MUSIC_FADE * 1000))
        self.music_cue = None
        if mode == "title":
            self.music_next_at = now + MUSIC_FADE * 0.5
        elif mode == "game":
            self.music_next_at = now + self.rnd.uniform(6.0, 14.0)    # let the table settle before the first track

    def music_sting_after(self, cid, now, delay=0.0):
        """End of a game: fade the music and play this one-off (VICTORY / DEFEAT), `delay` seconds from now."""
        self.music_mode = "sting"
        self.music_next_at = None
        if not self.ready:
            return
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.fadeout(int(min(delay, MUSIC_FADE) * 1000) or 300)
        self.music_sting = (cid, now + delay)

    def _game_tracks(self):
        return sorted(c for c, cue in self.cues.items() if c.startswith("music.game") and getattr(cue, "paths", None))

    def _start_track(self, cid, now, loops=0):
        cue = self.cues.get(cid)
        paths = getattr(cue, "paths", None)
        if not paths:
            return False
        try:
            pygame.mixer.music.load(paths[0])
            pygame.mixer.music.set_volume(self.stream_gain("music", now) * cue.gain)
            pygame.mixer.music.play(loops=loops, fade_ms=1500)
        except pygame.error:
            return False
        self.music_cue = cid
        return True

    def _update_music(self, now):
        busy = pygame.mixer.music.get_busy()
        if self.music_sting and now >= self.music_sting[1]:
            cid = self.music_sting[0]
            self.music_sting = None
            self._start_track(cid, now)
        elif self.music_mode == "title" and not busy and self.music_next_at is not None and now >= self.music_next_at:
            self._start_track("music.title", now, loops=-1)
            self.music_next_at = None
        elif self.music_mode == "game" and not busy:
            if self.music_next_at is None:                              # a track just ended: rest a while
                self.music_next_at = now + self.rnd.uniform(*MUSIC_GAP)
                self.music_cue = None
            elif now >= self.music_next_at:
                if not self.music_order:
                    self.music_order = self._game_tracks()
                    self.rnd.shuffle(self.music_order)
                if self.music_order:
                    self._start_track(self.music_order.pop(), now)
                self.music_next_at = None
        if busy and self.music_cue:
            pygame.mixer.music.set_volume(self.stream_gain("music", now) * self.cues[self.music_cue].gain)
