# SPDX-License-Identifier: GPL-3.0-or-later
"""Build sounds/*.ogg from CC0 recordings (Round AU1: the dark-fantasy sound overhaul; patch 49 / AU2: the palette rebuild).

    python tools/build_sounds.py                 # download what's missing, build everything, rewrite sounds/cues.json + CREDITS.txt
    python tools/build_sounds.py --only spell.cast amb.graveyard
    python tools/build_sounds.py --cache D:\\sound_cache

A DEVELOPER tool: it needs numpy, scipy and ffmpeg on the PATH. The game itself never runs it and only plays the .ogg files it writes.
Every recording comes from tools/sound_sources.json (all CC0-1.0). Each sound is a RECIPE below: layers cut from those recordings,
pitched, filtered, mixed, put through a synthetic stone-hall reverb and levelled. Change a recipe and rebuild; the output is the same
every time (fixed seeds).

The sound of the game (docs/SOUND_BIBLE.md, AU2): dark fantasy from CC0 recordings - magic is energy and air, bodies are leather and
metal, harm is flesh, turns are horns, the end is crows; bells only where Karl kept them (vs.reveal, boot.splash). AU1's "everything
is stone, iron, parchment and bells in a cold church" is what his 7 Oct review sent back (36 of 53 cues). Patch 49 built the 37
sounds he picked on the candidates page (claude/ROUND_AU2_CANDIDATES_2026-10-07.md); the nine he marked "none of these" (card.play,
card.discard, ui.dialog_open, destroy of a land, land.play for island / mountain / plains / other, music.victory) keep AU1's recipe
until the second candidates page is picked.

Typed cues (AU2): card.land.<creature family | artifact | enchantment>, land.play.<forest | swamp>, destroy.<creature | artifact |
enchantment>, spell.cast.commander. The table picks them with cue_types.py; a card whose type isn't known plays the parent cue.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES = os.path.join(HERE, "sound_sources.json")
SOUNDS = os.path.join(ROOT, "sounds")
SR = 44100
UA = {"User-Agent": "Manticore-sound-build/1.0 (+CC0 sources, see tools/sound_sources.json)"}


# ---- fetching -------------------------------------------------------------------------------------------------------------------------

def fetch(key, src, cache):
    """The folder holding source `key`, downloading and unpacking it the first time."""
    folder = os.path.join(cache, key)
    if os.path.isfile(os.path.join(folder, ".done")):
        return folder
    os.makedirs(folder, exist_ok=True)
    urls = src.get("urls") or [src["url"]]               # AU2: an OpenGameArt page with several files lists them all ("kind": "files")
    digest = hashlib.sha256()
    for url in urls:
        print(f"  downloading {key}: {url}")
        data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600).read()
        digest.update(data)
        name = urllib.parse.unquote(url.rsplit("/", 1)[1])
        low = name.lower()
        if src["kind"] == "zip" or (src["kind"] == "files" and low.endswith(".zip")):
            zipfile.ZipFile(io.BytesIO(data)).extractall(folder)
        elif src["kind"] == "7z" or (src["kind"] == "files" and low.endswith(".7z")):
            import py7zr                                 # a few sources are 7z
            py7zr.SevenZipFile(io.BytesIO(data)).extractall(folder)
        else:
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
    with open(os.path.join(folder, ".done"), "w") as f:
        f.write(digest.hexdigest())
    return folder


_RAW = {}
FORMAT_RANK = {".flac": 0, ".wav": 1, ".ogg": 2, ".aif": 2, ".aiff": 2, ".mp3": 3}     # the same recording in two formats: the lossless one


def load(ref, ctx, raw=False):
    """'key' or 'key:part-of-file-name' -> float32 stereo array (n, 2) at 44.1 kHz. raw=True keeps the recordist's leading silence
    (an ambience bed or a music track is cut by the clock, not by its first sound)."""
    if (ref, raw) in _RAW:
        return _RAW[(ref, raw)]
    key, _, part = ref.partition(":")
    folder = fetch(key, ctx["sources"][key], ctx["cache"])
    files = []
    for dp, _dn, fns in os.walk(folder):
        if "__MACOSX" in dp:
            continue
        files += [os.path.join(dp, fn) for fn in fns
                  if fn.lower().endswith((".ogg", ".wav", ".flac", ".mp3", ".aif", ".aiff")) and not fn.startswith("._")]
    if part:
        files = [f for f in files if part.lower() in os.path.basename(f).lower()]
    if ctx["sources"][key].get("prefer_lossless"):           # AU2's sources (the order Karl's candidates were built with)
        files.sort(key=lambda f: (FORMAT_RANK.get(os.path.splitext(f)[1].lower(), 4), len(os.path.basename(f)), f))
    else:                                                    # AU1's: unchanged, so a rebuild of a kept sound picks the same file
        files.sort(key=lambda f: (len(os.path.basename(f)), f))
    if not files:
        raise SystemExit(f"no file matching {ref!r} in {folder}")
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", files[0], "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(out, np.float32).reshape(-1, 2).copy()
    x -= x.mean(axis=0)                                  # DC
    if not raw:
        x = head(x)                                      # every layer starts when its sound does, not after the recordist's pause
    _RAW[(ref, raw)] = x
    ctx["used"].add(key)
    return x


# ---- DSP ------------------------------------------------------------------------------------------------------------------------------

def db(v):
    return 10 ** (v / 20)


def mono(x):
    return np.repeat(x.mean(axis=1, keepdims=True), 2, axis=1)


def cut(x, start=0.0, length=None):
    a = int(start * SR)
    b = len(x) if length is None else a + int(length * SR)
    return x[a:b].copy()


def onsets(x, thresh_db=-30, gap=0.25, jump_db=8.0):
    """Times (s) where the sound strikes: the 10 ms level jumps by `jump_db` over the quietest of the 120 ms before it,
    and is within `thresh_db` of the loudest moment."""
    hop = int(0.01 * SR)
    m = np.abs(x).mean(axis=1)
    frames = len(m) // hop
    if frames < 3:
        return [0.0]
    lev = 20 * np.log10(np.sqrt((m[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)) + 1e-9)
    top = lev.max()
    hits, last = ([0.0], 0.0) if lev[:3].max() > top + thresh_db else ([], -1e9)    # it starts loud: that's a strike too
    for i in range(1, frames):
        before = lev[max(0, i - 12):i].min()
        if lev[i] > top + thresh_db and lev[i] - before > jump_db and (i * hop / SR) - last > gap:
            k = i                                          # walk to the local peak, then back to where the rise began
            while k > 0 and lev[k - 1] < lev[k] - 1.0 and lev[k - 1] > before + 2:
                k -= 1
            hits.append(k * hop / SR)
            last = i * hop / SR
    return hits


def head(x, floor_db=-30, pre=0.004):
    """Cut the silence before a recording starts: everything before the first moment within `floor_db` of its peak."""
    a = np.abs(x).max(axis=1)
    idx = np.where(a > db(floor_db) * (a.max() + 1e-12))[0]
    if not len(idx):
        return x
    return x[max(0, idx[0] - int(pre * SR)):].copy()


def strike(x, n=0, pre=0.01, length=2.0, thresh_db=-24):
    """The n-th strike of a recording with several (bells, heartbeats, knocks)."""
    hits = onsets(x, thresh_db) or [0.0]
    if isinstance(n, float):                               # a time: the strike nearest to it
        t = min(hits, key=lambda h: abs(h - n))
    else:
        t = hits[min(n, len(hits) - 1)]
    return cut(x, max(0.0, t - pre), length)


def pitch(x, semis):
    """Resample: lower AND slower, like a tape slowed down - the classic way to make a sound bigger and darker."""
    if not semis:
        return x
    ratio = 2 ** (semis / 12)
    n = max(8, int(len(x) / ratio))
    return signal.resample_poly(x, 1000, int(round(1000 * ratio)), axis=0).astype(np.float32)[:n] if ratio != 1 else x


def filt(x, hp=None, lp=None, order=4):
    if hp:
        x = signal.sosfilt(signal.butter(order, hp, "highpass", fs=SR, output="sos"), x, axis=0)
    if lp:
        x = signal.sosfilt(signal.butter(order, lp, "lowpass", fs=SR, output="sos"), x, axis=0)
    return x.astype(np.float32)


def shelf_low(x, freq=180, gain_db=4.0):
    """A low boost: the band below `freq` added back in, for weight."""
    lowpart = signal.sosfilt(signal.butter(2, freq, "lowpass", fs=SR, output="sos"), x, axis=0)
    return (x + (db(gain_db) - 1) * lowpart).astype(np.float32)


def fade(x, fin=0.003, fout=0.05):
    x = x.copy()
    a, b = int(fin * SR), int(fout * SR)
    if a:
        x[:a] *= np.linspace(0, 1, a)[:, None]
    if b and b < len(x):
        x[-b:] *= (np.linspace(1, 0, b) ** 2)[:, None]
    return x


def reverse(x):
    return x[::-1].copy()


def place(dst, src, at, gain_db=0.0):
    a = int(at * SR)
    need = a + len(src)
    if need > len(dst):
        dst = np.concatenate([dst, np.zeros((need - len(dst), 2), np.float32)])
    dst[a:a + len(src)] += src * db(gain_db)
    return dst


def width(x, amount=0.3, delay_ms=11):
    """Mono-ish sources get a little stereo: a short delayed copy on one side (Haas)."""
    d = int(delay_ms * SR / 1000)
    m = x.mean(axis=1)
    side = np.concatenate([np.zeros(d, np.float32), m[:-d]]) if d < len(m) else m
    out = np.stack([m + amount * (side - m) * 0.5, m - amount * (side - m) * 0.5], axis=1)
    return out.astype(np.float32)


_IR = {}


def impulse(kind):
    """A synthetic stone-room impulse response, stereo, fixed seed. 'room' a small vault, 'hall' a nave, 'crypt' long and dark."""
    if kind in _IR:
        return _IR[kind]
    rt60, pre, damp, er = {"room": (0.7, 0.008, 5200, 6), "hall": (2.3, 0.022, 3800, 10), "crypt": (3.6, 0.035, 2600, 12)}[kind]
    rng = np.random.default_rng({"room": 1, "hall": 2, "crypt": 3}[kind])
    n = int((rt60 * 1.1 + pre) * SR)
    t = np.arange(n) / SR
    ir = np.zeros((n, 2), np.float32)
    env = np.exp(-6.91 * t / rt60)
    for ch in range(2):
        noise = rng.standard_normal(n).astype(np.float32) * env
        # air and stone absorb the highs as the tail goes on: blend from a brighter to a darker copy
        bright = signal.sosfilt(signal.butter(2, damp, "lowpass", fs=SR, output="sos"), noise)
        dark = signal.sosfilt(signal.butter(2, damp / 4, "lowpass", fs=SR, output="sos"), noise)
        k = np.clip(t / rt60, 0, 1)
        tail = (1 - k) * bright + k * dark
        tail[: int(pre * SR)] = 0
        ir[:, ch] = tail
        for j in range(er):                               # early reflections off near walls
            at = int((pre * (0.3 + 0.7 * rng.random()) + 0.002 * j) * SR)
            ir[at, ch] += (0.6 - 0.04 * j) * rng.choice([-1, 1])
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True))
    _IR[kind] = ir
    return ir


def reverb(x, kind="room", wet_db=-12.0, dry=1.0, tail=True):
    ir = impulse(kind)
    wet = np.stack([signal.fftconvolve(x[:, 0], ir[:, 0]), signal.fftconvolve(x[:, 1], ir[:, 1])], axis=1).astype(np.float32)
    out = np.zeros_like(wet)
    out[:len(x)] += x * dry
    out += wet * db(wet_db)
    if not tail:
        out = out[:len(x)]
    return trim_tail(out)


def trim_tail(x, floor_db=-60):
    a = np.abs(x).max(axis=1)
    keep = np.where(a > db(floor_db) * (a.max() + 1e-9))[0]
    return x[: (keep[-1] + 1 if len(keep) else 1)]


def soft_limit(x, ceiling_db=-1.0):
    c = db(ceiling_db)
    return (np.tanh(x / c) * c).astype(np.float32)


def loudness(x):
    """Integrated loudness, LUFS (ITU-R BS.1770 K-weighting, no gating below 0.4 s: a short cue is measured whole)."""
    hs = signal.lfilter([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585], x, axis=0)
    hp = signal.lfilter([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621], hs, axis=0)
    ms = (hp ** 2).mean(axis=0).sum()
    return -0.691 + 10 * np.log10(ms + 1e-12)


def limit(x, ceiling_db=-1.0, release=0.06):
    """A peak limiter: turns down only the moments above the ceiling (instant attack, smooth release), so a sharp hit can be
    made as loud as the others without clipping."""
    c = db(ceiling_db)
    a = np.abs(x).max(axis=1)
    need = np.minimum(1.0, c / np.maximum(a, 1e-12))
    # smooth: a running minimum over a short look-around, then a release slope
    w = max(1, int(0.002 * SR))
    need = -signal.convolve(-need, np.ones(w) / w, mode="same") if w > 1 else need
    g = np.empty_like(need)
    cur, k = 1.0, np.exp(-1.0 / (release * SR))
    for i in range(len(need)):                               # numpy-free loop is slow; chunk by blocks where it matters
        t = need[i]
        cur = t if t < cur else t + (cur - t) * k
        g[i] = cur
    return (x * g[:, None]).astype(np.float32)


def level(x, lufs=None, peak_db=-1.0):
    """To a target loudness (LUFS) with peaks held under `peak_db` by the limiter (a few dB of it at most)."""
    if lufs is not None:
        x = x * db(lufs - loudness(x))
    pk = 20 * np.log10(np.abs(x).max() + 1e-12)
    if pk > peak_db + 6:                                     # more than 6 dB of limiting would squash it: back off the rest
        x = x * db(peak_db + 6 - pk)
    if np.abs(x).max() > db(peak_db):
        x = limit(x, peak_db)
    return np.clip(x, -db(peak_db), db(peak_db)).astype(np.float32)


def loop_seam(x, xfade=2.0):
    """Make a seamless loop: the last `xfade` seconds are blended into the start."""
    n = int(xfade * SR)
    body, end = x[:-n].copy(), x[-n:]
    w = np.linspace(0, 1, n)[:, None]
    body[:n] = body[:n] * np.sqrt(w) + end * np.sqrt(1 - w)
    return body


def write_ogg(x, path, quality=5):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = np.clip(x, -1, 1).astype(np.float32).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-i", "-",
                    "-c:a", "libvorbis", "-q:a", str(quality), "-map_metadata", "-1", "-fflags", "+bitexact", path],
                   input=pcm, check=True)


# ---- the recipes ----------------------------------------------------------------------------------------------------------------------
# Each returns a list of variants (arrays). L(ref) loads a recording. Levels: UI quiet (-30 LUFS), table sounds -24, big moments -18,
# stingers -16; the cue's gain_db in cues.json trims from there. recipes() holds AU1's sounds that Karl kept (and the four he sent back
# twice, until round 2 of the candidates page is picked); recipes_au2() the 37 he picked on 7 Oct.

def recipes(L):
    R = {}

    def card(ref, lp=6500, semis=-2):
        return filt(pitch(L(ref), semis), hp=120, lp=lp)

    # -- interface --
    R["ui.hover"] = lambda: [level(fade(filt(cut(L(f"kenney-casino:card-slide-{i}"), 0, 0.09), hp=900, lp=6000), 0.002, 0.04), -36, -18)
                             for i in (1, 3, 5)]
    R["ui.click"] = lambda: [level(reverb(fade(mix(shelf_low(filt(pitch(L(f"kenney-impact:impactWood_light_00{i}"), -5), hp=70, lp=5000), 160, 3),
                                                   ), 0.001, 0.06),
                                          "room", -16), -28, -12) for i in (0, 2, 4)]
    R["ui.dialog_open"] = lambda: [level(reverb(fade(filt(pitch(L("kenney-rpg:bookOpen"), -3), hp=90, lp=5500), 0.002, 0.08), "room", -15), -28, -12)]
    R["ui.dialog_close"] = lambda: [level(reverb(fade(filt(pitch(L("kenney-rpg:bookClose"), -3), hp=90, lp=5500), 0.002, 0.08), "room", -15), -28, -12)]
    R["ui.menu_select"] = lambda: [level(reverb(mix(shelf_low(filt(pitch(L("oga-boulder"), -4), lp=2500), 150, 4),
                                                    (filt(pitch(L("kenney-rpg:metalLatch"), -5), hp=200, lp=5000), 0.02, -6)), "hall", -12), -20, -6)]

    # -- cards --
    R["card.pickup"] = lambda: [level(fade(card(f"kenney-casino:card-slide-{i}", 7000, -1)[: int(0.16 * SR)], 0.002, 0.06), -30, -14)
                                for i in (2, 4, 6, 8)]
    R["card.play"] = lambda: [level(reverb(mix(card(f"kenney-casino:card-shove-{i}", 6500, -2),
                                               (filt(pitch(L(f"oga-swishes:swish-{s}"), -7), hp=80, lp=2500), 0.0, -12)), "room", -16), -24, -8)
                              for i, s in ((1, 6), (2, 8), (3, 10), (4, 12))]
    R["land.play"] = lambda: [level(reverb(mix(card(f"kenney-casino:card-place-{i}", 5500, -3),
                                               (shelf_low(filt(pitch(L(f"kenney-impact:impactMining_00{i - 1}"), -8), lp=1600), 120, 5), 0.003, -3),
                                               (filt(pitch(L(f"oga-breaking:rock_falling_0{i}"), -5), hp=300, lp=4000), 0.03, -18)),
                                           "room", -14), -22, -6) for i in (1, 2, 3, 4)]
    R["card.discard"] = lambda: [level(reverb(fade(card(f"kenney-casino:card-shove-{i}", 5500, -4), 0.002, 0.1), "room", -16), -28, -10)
                                 for i in (2, 3, 4)]

    # -- magic (AU2 replaced the rest; exile is a Keep) --
    R["exile"] = lambda: [level(reverb(mix(filt(pitch(L(f"oga-swishes:swish-{s}"), -6), hp=80, lp=3000),
                                           (fade(filt(pitch(cut(L("oga-drain"), 0, 1.4), -5), hp=120, lp=3500), 0.05, 0.6), 0.05, -8)),
                                       "crypt", -9), -22, -6) for s in (9, 11, 13)]

    # -- combat --
    R["combat.attack"] = lambda: [level(reverb(mix(filt(pitch(L("fs-sword-draw"), -2), hp=150, lp=6000),
                                                   (shelf_low(filt(pitch(L("fs-distant-boom"), -1), lp=900), 90, 4), 0.25, -2)), "hall", -10), -19, -4),
                                  level(reverb(mix(filt(pitch(L("kenney-rpg:drawKnife1"), -5), hp=150, lp=6000),
                                                   (shelf_low(filt(pitch(L("fs-distant-boom"), -2), lp=900), 90, 4), 0.2, -2)), "hall", -10), -19, -4)]

    # -- turns and the end --
    R["vs.reveal"] = lambda: [level(reverb(mix(filt(pitch(L("oga-swordclash:sword_clash.1"), -6), hp=80, lp=5000),
                                               (shelf_low(filt(pitch(L("fs-distant-boom"), -3), lp=800), 80, 6), 0.0, 0),
                                               (fade(filt(pitch(strike(L("fs-toll"), 9.7, 0.02, 3.0), -5), lp=2000), 0.005, 1.0), 0.05, -8)),
                                           "crypt", -9), -16, -2)]
    R["boot.splash"] = lambda: [level(reverb(mix(fade(filt(cut(L("fs-drone-rumble"), 1.0, 3.2), lp=700), 1.2, 1.2),
                                                 (fade(filt(pitch(strike(L("fs-toll-distant"), 0, 0.02, 3.0), -2), hp=80, lp=2500), 0.005, 1.2), 0.6, -4)),
                                             "crypt", -10), -22, -6)]
    return R


# ---- AU2: the sounds Karl picked on the candidates page (7 Oct 2026) -------------------------------------------------------------------
# Each recipe is the candidate he picked, number for number (scratchpad candidates.py in the AU2 session; claude/ROUND_AU2_PICKS_ROUND2_
# 2026-10-08.md lists cue -> letter -> title). Built without AU1's TIGHT trim (the recipes trim their own onsets where needed), and a
# cue on the sfx / ui buses with cooldown_ms < 100 is cut at 1.15 s with a fade (patch 47's guard), exactly as the candidates were.

AU2_CAP_SECONDS = 1.15


def recipes_au2(L):
    R = {}

    def sec(x, length):
        """The first `length` seconds, faded out over the last 15% (never longer than 80 ms)."""
        y = cut(x, 0, length)
        return fade(y, 0.002, min(0.08, length * 0.15))

    def thud(ref="kenney-impact:impactSoft_heavy_000", semis=-5, lp=900, gain=4):
        return shelf_low(filt(pitch(L(ref), semis), lp=lp), 120, gain)

    def hits_from(ref, n, length=0.9, pre=0.01, thresh=-24):
        """The first n strikes of a long recording of many, each cut to `length`."""
        x = L(ref)
        hs = onsets(x, thresh) or [0.0]
        out = []
        for t in hs:
            if len(out) >= n:
                break
            y = cut(x, max(0.0, t - pre), length)
            if len(y) > SR * 0.1:
                out.append(fade(y, 0.002, min(0.1, length * 0.2)))
        while len(out) < n:
            out.append(out[-1] if out else sec(x, length))
        return out

    def tight(x):
        return head(x, -24, 0.002)

    # -- magic --
    R["spell.cast"] = lambda: [level(reverb(mix(filt(pitch(sec(L("oga2-magic-spell-sfx:magical_%d" % i), 0.75), -2), hp=120, lp=9000),
                                                (filt(pitch(L("oga2-swish-bamboo-stick-weapon-swhoshes:swosh-%02d" % s), -4), hp=200, lp=6000), 0.0, -8)),
                                            "room", -16), -20, -5) for i, s in ((1, 13), (5, 15), (7, 19))]
    R["spell.resolve"] = lambda: [level(reverb(fade(filt(pitch(cut(L("oga2-magic-spell-sfx:magical_%d" % i), 0.0, 0.55), -1), hp=150, lp=9000),
                                                    0.002, 0.15), "room", -14), -25, -8) for i in (6, 2, 3)]
    R["spell.fizzle"] = lambda: [level(reverb(mix(reverse(fade(filt(pitch(sec(L("oga2-magic-spell-sfx:magical_2"), 0.55), -2), hp=150, lp=6000),
                                                               0.15, 0.003)),
                                                  (filt(pitch(L("oga2-27-metal-audio-samples-sfx:dull_metal_collision_03"), -5), lp=1800), 0.55, -3)),
                                              "room", -14), -26, -8)]
    R["ability.trigger"] = lambda: [level(reverb(fade(filt(pitch(cut(L("oga2-spell-sounds:electricspell2.ogg"), t, 0.3), -6), hp=300, lp=7000),
                                                      0.002, 0.08), "room", -18), -28, -10) for t in (0.5, 3.0, 6.0)]
    R["token.create"] = lambda: [level(reverb(mix(filt(pitch(L("kenney-rpg:cloth%d" % i), -4), hp=150, lp=4000),
                                                  (filt(pitch(sec(L("oga2-rpg-sound-pack:magic1"), 0.8), -2), hp=150, lp=6000), 0.0, -5)),
                                              "room", -15), -28, -10) for i in (1, 3)]

    # -- arrivals by type: card.land is the creature sound (humanoids and any creature outside a family, and a card whose data
    #    isn't loaded); card.land.artifact / .enchantment by arrival kind; the creature families come from round 2 --
    def arrival(base_i, layer, at=0.01, gain=-4):
        return level(reverb(mix(filt(pitch(L("kenney-casino:card-place-%d" % base_i), -3), hp=120, lp=7000), (layer, at, gain)), "room", -15),
                     -23, -7)
    R["card.land"] = lambda: [arrival(i, mix(filt(pitch(L("oga2-footsteps-leather-cloth-armor:step_lth%d" % s), -3), lp=3000),
                                             (filt(pitch(sec(L("oga2-80-cc0-creature-sfx:grunt_%02d" % g), 0.5), -3), hp=100, lp=4000), 0.05, -4)), 0.0, -2)
                              for i, s, g in ((1, 1, 2), (2, 2, 4), (3, 4, 1))]
    R["card.land.artifact"] = lambda: [arrival(i, mix(filt(pitch(L("oga2-metal-impact-sounds:clink%d" % c), -2), hp=400, lp=9000),
                                                      (filt(L("oga2-gold-coin"), hp=500, lp=9000), 0.03, -8)), 0.02, -6) for i, c in ((1, 1), (2, 2), (3, 3))]
    R["card.land.enchantment"] = lambda: [arrival(i, filt(pitch(cut(L("oga2-flight-or-spell-sound"), t, 0.8), -2), hp=300, lp=8000), 0.02, -8)
                                          for i, t in ((1, 0.0), (2, 0.6), (3, 1.2))]

    # -- lands by type: land.play stays AU1's until round 2 gives "other" (and island / mountain / plains) --
    def land(base_i, layer, gain=-3, at=0.02):
        return level(reverb(mix(filt(pitch(L("kenney-casino:card-place-%d" % base_i), -3), hp=120, lp=6000), (layer, at, gain)), "room", -14), -22, -6)
    R["land.play.forest"] = lambda: [land(1, mix(filt(cut(L("oga2-forest-bird-sounds:Forest Birds 9"), 0.0, 0.7), hp=800, lp=9000),
                                                 (filt(L("oga2-different-steps-on-wood-stone-leaves-gra:leaves02"), hp=300, lp=8000), 0.0, -2)), -8)]
    R["land.play.swamp"] = lambda: [land(3, mix(shelf_low(filt(L("oga2-25-cc0-mud-sfx:mud_03"), lp=3000), 150, 4),
                                                (filt(L("oga2-40-cc0-water-splash-slime-sfx:bubble_01"), hp=200, lp=6000), 0.15, -4)))]

    # -- destruction by type: destroy is the creature break (and a permanent whose data isn't loaded); a land keeps AU1's until round 2 --
    def wreck(layer, verb="room", lufs=-20, peak=-5):
        return level(reverb(layer, verb, -13), lufs, peak)
    R["destroy"] = lambda: [wreck(mix(filt(pitch(L("oga2-37-hits-punches:hit%02d" % i), -3), hp=80, lp=6000),
                                      (filt(pitch(L("oga2-8-wet-squish-slurp-impacts:impactsplat%02d" % s), -2), hp=100, lp=6000), 0.04, -4)))
                            for i, s in ((5, 3), (12, 6), (24, 1))]
    R["destroy.artifact"] = lambda: [wreck(mix(filt(pitch(L("oga2-100-cc0-metal-and-wood-sfx:metal_falling_0%d" % i), -2), hp=100, lp=9000),
                                               (filt(L("oga2-glass-break"), hp=400, lp=9000), 0.05, -10))) for i in (1, 2)]
    R["destroy.enchantment"] = lambda: [wreck(mix(reverse(fade(filt(pitch(sec(L("oga2-magic-spell-sfx:magical_%d" % m), 0.7), -2), hp=200, lp=8000),
                                                               0.2, 0.003)),
                                                  (filt(L("oga2-various-paper-sound-effects:Paper Ripped - 1"), hp=300, lp=8000), 0.55, -4)))
                                        for m in (4, 1)]

    # -- harm --
    R["damage.creature"] = lambda: [level(reverb(mix(filt(pitch(sec(L("oga2-fantasy-weapons-and-apparel-sfx-library:sword-knife-clash-%02d" % i), 0.8),
                                                                -2), hp=120, lp=9000),
                                                     (filt(pitch(L("oga2-37-hits-punches:hit%02d" % h), -3), lp=2000), 0.0, -6)), "room", -15), -21, -5)
                                    for i, h in ((6, 9), (13, 25), (18, 32), (30, 12))]
    R["damage.player"] = lambda: [level(reverb(mix(shelf_low(filt(pitch(L("oga2-punch:qubodupPunch%02d" % i), -3), lp=3000), 100, 6),
                                                   (filt(pitch(sec(L("oga2-pain-sounds-by-emopreben:Track %d" % p), 0.7), -2), hp=150, lp=5000), 0.02, -5),
                                                   (thud("kenney-impact:impactSoft_heavy_000", -8, 500, 6), 0.0, -4)), "room", -14), -17, -3)
                                  for i, p in ((2, 2), (4, 5), (5, 1))]
    R["life.loss"] = lambda: [level(reverb(mix(fade(filt(pitch(cut(L("oga2-energy-drain:qubodup-PowerDrain.flac"), 0.0, 1.0), -4), hp=100, lp=4000),
                                                    0.01, 0.4),
                                               (shelf_low(filt(pitch(L("oga2-heartbeat-sounds:heartbeat_fast_0"), -3), lp=400), 100, 5), 0.5, -2)),
                                           "room", -14), -22, -6)]
    R["life.gain"] = lambda: [level(reverb(mix(filt(L("oga2-fantasy-sound-effects-tinysized-sfx:vial-glass-uncork-01"), hp=300, lp=9000),
                                               (filt(sec(L("oga2-liquid-bottle-drink-set:swallow-03"), 0.4), hp=150, lp=6000), 0.3, -2),
                                               (fade(filt(pitch(cut(L("oga2-magic-spell-sfx:magical_6"), 0.2, 0.6), -1), hp=400, lp=9000), 0.05, 0.2), 0.45, -8)),
                                           "room", -15), -26, -8)]
    R["counter.add"] = lambda: [level(fade(mix(filt(pitch(L("oga2-12-coin-sound-effects:coin.%d" % c), -3), hp=400, lp=9000),
                                               (filt(pitch(L("oga2-100-cc0-metal-and-wood-sfx:hammer_0%d" % h), -2), hp=200, lp=8000), 0.0, -3))[: int(0.3 * SR)],
                                           0.002, 0.08), -30, -12) for c, h in ((1, 2), (3, 3))]
    R["counter.remove"] = lambda: [level(fade(filt(pitch(tight(L("oga2-metal-impact-sounds:clink%d" % i)), -8), hp=200, lp=6000)[: int(0.3 * SR)],
                                              0.002, 0.1), -30, -12) for i in (3, 1)]
    R["combat.block"] = lambda: [level(reverb(mix(shelf_low(filt(pitch(L("oga2-35-wooden-cracks-hits-destructions:impactwood%02d" % w), -4), lp=3000),
                                                            120, 4),
                                                  (filt(pitch(L("oga2-100-cc0-metal-and-wood-sfx:metal_sheet_0%d" % s), -3), hp=200, lp=7000), 0.01, -5)),
                                              "room", -13), -21, -6) for w, s in ((11, 2), (13, 5), (15, 1))]

    # -- turns and the end --
    R["phase.turn_mine"] = lambda: [level(reverb(fade(filt(pitch(hits_from("mus2-war-horns", 3, 1.6, 0.02, -20)[0], 0), hp=80, lp=7000), 0.01, 0.5),
                                                 "hall", -14), -20, -4)]
    R["phase.turn_theirs"] = lambda: [level(reverb(fade(filt(cut(L("oga2-horde-war-drums-loop"), 0.0, 1.1), lp=1500), 0.01, 0.4), "crypt", -9),
                                            -28, -10)]
    R["game.win"] = lambda: [level(reverb(mix(fade(filt(cut(L("mus2-war-horns"), 0.0, 3.5), hp=80, lp=7000), 0.01, 1.0),
                                              (filt(pitch(L("oga2-an-interesting-snare-drum-sound"), -2), lp=7000), 0.0, -4),
                                              (filt(pitch(L("oga2-an-interesting-snare-drum-sound"), -2), lp=7000), 0.5, -4)), "hall", -11), -16, -2)]
    R["game.lose"] = lambda: [level(reverb(mix(filt(L("oga2-crow-caw"), hp=300, lp=7000),
                                               (filt(pitch(L("oga2-crow-caw"), -3), hp=300, lp=7000), 1.1, -4),
                                               (fade(filt(pitch(cut(L("oga2-undead-moans:undead-2"), 0.0, 1.9), -2), hp=100, lp=2500), 0.1, 0.8), 0.6, -6),
                                               (fade(filt(cut(L("oga2-rumbling-sound-effects:rumbling17"), 20.0, 5.0), lp=220), 0.5, 2.0), 0.0, -4)),
                                           "crypt", -10), -16, -2)]
    R["rewind"] = lambda: [level(reverb(mix(reverse(fade(filt(pitch(L("oga2-fantasy-sound-effects-tinysized-sfx:tube-plastic-whoosh-01"), -4),
                                                              hp=100, lp=5000), 0.2, 0.003)),
                                            (reverse(fade(filt(pitch(L("oga2-fantasy-sound-effects-tinysized-sfx:tube-plastic-whoosh-02"), -6),
                                                               hp=100, lp=5000), 0.2, 0.003)), 0.25, -2),
                                            (filt(pitch(L("kenney-rpg:metalClick"), -2), hp=300, lp=8000), 0.62, -6)), "hall", -14), -26, -8)]

    # -- cards, taps --
    R["tap"] = lambda: [level(fade(filt(pitch(cut(L("oga2-rpg-sound-pack:wood-small"), 0.0, 0.15), -3 + d), hp=150, lp=6000), 0.002, 0.05), -34, -16)
                        for d in (0, 2)]
    R["untap"] = lambda: [level(fade(filt(L("oga2-footsteps-leather-cloth-armor:step_cloth%d" % i), hp=200, lp=6000)[: int(0.18 * SR)], 0.004, 0.08),
                                -36, -18) for i in (1, 3)]
    R["flurry"] = lambda: [level(reverb(mix(filt(cut(L("kenney-casino:card-slide-1"), 0.0, 0.3), hp=150, lp=9000),
                                            (filt(cut(L("kenney-casino:card-slide-3"), 0.0, 0.3), hp=150, lp=9000), 0.05, -1),
                                            (filt(cut(L("kenney-casino:card-slide-5"), 0.0, 0.3), hp=150, lp=9000), 0.11, -2)), "room", -18), -28, -10)]
    R["card.draw"] = lambda: [level(reverb(mix(filt(pitch(L("oga2-card-game-sounds:draw"), d), hp=200, lp=9000),
                                               (filt(pitch(tight(L("kenney-casino:card-slide-%d" % i)), -1), hp=150, lp=9000), 0.03, -4)), "room", -20), -28, -12)
                              for d, i in ((0, 2), (-1, 4), (1, 6), (-2, 8))]
    R["deck.shuffle"] = lambda: [level(reverb(mix(filt(L("oga2-playing-card-sounds:shuffle.wav"), hp=150, lp=9000),
                                                  (filt(L("oga2-58-random-sound-effects:Cards_Riffle_Shuffle_01"), hp=150, lp=9000), 0.75, -1)),
                                              "room", -18), -26, -8)]

    # -- interface --
    R["ui.error"] = lambda: [level(reverb(fade(filt(pitch(L("oga2-card-game-sounds:error"), -4), hp=80, lp=2500), 0.001, 0.1), "room", -14), -24, -9)]
    R["ui.menu_hover"] = lambda: [level(fade(filt(cut(L("oga2-58-random-sound-effects:Card_Swipe_02"), 0.0, 0.12), hp=800, lp=9000), 0.004, 0.05),
                                        -34, -18)]
    return R


def ambiences_au2(L):
    """The four rooms Karl picked (all C on the page): 40 s loops from untrimmed recordings, seam cross-faded."""
    A = {}

    def bed(ref, start, length, hp=40, lp=4000, gain=0.0):
        return filt(cut(L(ref, raw=True), start, length + 2.0), hp=hp, lp=lp) * db(gain)

    def finish(x, length, lufs):
        x = x[: int((length + 2.0) * SR)]
        return level(loop_seam(x, 2.0), lufs, -14)

    A["amb.citadel"] = lambda: finish(mix(bed("oga2-loopable-dungeon-ambience", 10, 40, 40, 3500), (bed("oga2-fire-crackling", 0, 2.9, 80, 5000, -8), 12.0, 0),
                                          (bed("oga2-fire-crackling", 0, 2.9, 80, 5000, -10), 31.0, 0)), 40, -33)
    A["amb.title"] = lambda: finish(mix(bed("oga2-mild-wind-background-noise", 2, 40, 30, 400, -6),
                                        (reverb(filt(L("oga2-dark-ambiences:ambience-1"), lp=2500), "crypt", -8), 6.0, -12),
                                        (reverb(filt(L("oga2-dark-ambiences:ambience-5"), lp=2500), "crypt", -8), 27.0, -14)), 40, -36)
    A["amb.ruins"] = lambda: finish(mix(bed("oga2-wind:Wind.ogg", 0, 6.6, 40, 500, -2), (bed("oga2-wind:Wind2", 0, 6.7, 40, 500, -2), 6.5, 0),
                                        (bed("oga2-wind:Wind.ogg", 0, 6.6, 40, 500, -2), 13.0, 0), (bed("oga2-wind:Wind2", 0, 6.7, 40, 500, -2), 19.5, 0),
                                        (bed("oga2-wind:Wind.ogg", 0, 6.6, 40, 500, -2), 26.0, 0), (bed("oga2-wind:Wind2", 0, 6.7, 40, 500, -2), 32.5, 0),
                                        (reverb(filt(cut(L("oga2-tree-creaking"), 0.0, 3.0), hp=100, lp=2500), "crypt", -8), 15.0, -14),
                                        (reverb(filt(cut(L("oga2-water-drops", raw=True), 60.0, 2.0), hp=400, lp=6000), "crypt", -6), 8.0, -16),
                                        (reverb(filt(cut(L("oga2-water-drops", raw=True), 120.0, 2.0), hp=400, lp=6000), "crypt", -6), 29.0, -16)), 40, -32)
    A["amb.plain"] = lambda: finish(bed("oga2-loopable-dungeon-ambience", 50, 40, 40, 1200, 0), 40, -37)
    return A


def mix(base, *layers):
    """base plus (sound, at_seconds, gain_db) layers."""
    out = base.copy()
    for snd, at, g in layers:
        out = place(out, snd, at, g)
    return out


# ---- ambience: long, quiet, seamless loops -----------------------------------------------------------------------------------------

def ambiences(L):
    """AU1's rooms Karl kept (graveyard, cathedral); the other four are ambiences_au2()."""
    A = {}
    rng = np.random.default_rng(7)

    def bed(ref, start, length, hp=40, lp=4000, gain=0.0):
        return filt(cut(L(ref), start, length + 2.0), hp=hp, lp=lp) * db(gain)

    def sprinkle(base, ref_list, times, gain_db, lp, verb="crypt"):
        for t, ref in zip(times, ref_list):
            base = place(base, reverb(filt(L(ref), hp=200, lp=lp), verb, -6), t, gain_db)
        return base

    def finish(x, length, lufs):
        x = x[: int((length + 2.0) * SR)]
        return level(loop_seam(x, 2.0), lufs, -14)

    A["amb.graveyard"] = lambda: finish(sprinkle(mix(bed("fs-wind-howl", 2, 60, 60, 2600), (bed("fs-drone-low", 0, 40, 30, 300, -10), 0, 0)),
                                                 ["fs-crow", "fs-crow2", "fs-crow", "oga-treecreak"], [9.0, 27.5, 44.0, 33.0], -16, 3000), 60, -32)
    A["amb.cathedral"] = lambda: finish(mix(bed("fs-dungeon", 20, 62, 40, 3500),
                                            (bed("fs-choir-men", 5, 62, 80, 2500, -16), 0, 0),
                                            (bed("oga-drips", 0, 62, 300, 5000, -14), 0, 0)), 60, -33)
    return A


# ---- music: whole tracks, levelled and re-encoded -------------------------------------------------------------------------------------

MUSIC = {                                      # cue -> (source, start, length or None, fade-out)
    "music.title": ("mus-forest", 0, None, 3.0),
    "music.game1": ("mus-eternal", 0, None, 6.0),
    "music.game2": ("mus-lament", 0, None, 4.0),
    "music.game3": ("mus-cathedral-forest", 0, None, 4.0),
    "music.game4": ("mus-wastelands", 0, None, 6.0),
    "music.game5": ("mus-tragic", 0, None, 4.0),
    "music.game6": ("mus-cold-silence", 0, None, 6.0),           # AU2: Karl, 7 Oct - "Cold Silence -> game playlist"
    "music.victory": ("mus-victory", 0, 14.0, 4.0),             # AU1's until round 2 of the candidates page is picked
    "music.defeat": ("mus2-bittersweet", 0, None, 2.0),         # AU2: Bittersweet (Emma_MA), Karl's pick C
    "music.deck": ("mus-echoes", 0, None, 3.0),                 # AU2: "Echoes of the Past - fine for deck/game setup screen"
    "music.settings": ("mus-bleeding-out", 0, None, 4.0),       # AU2: "Bleeding Out - fine for credits/settings menus"
    "music.attack": ("mus-lament-war", 0, 45.0, 5.0),           # AU2: "Lament of the War - good music to play when attackers are
}                                                               #   declared"; the first 45 s, played on big attacks (Q2)
MUSIC_QUALITY = {"music.game6": 3, "music.settings": 3}         # long tracks: Vorbis q3 to stay inside test_au1's 32 MB budget
MUSIC_LUFS = -21


# ---- the manifest ---------------------------------------------------------------------------------------------------------------------

CUE_SETTINGS = {         # bus, gain_db, cooldown_ms, max_voices (+ duck)
    "ui.hover": ("ui", 0, 40, 1), "ui.click": ("ui", 0, 30, 2), "ui.error": ("ui", 0, 150, 1), "ui.dialog_open": ("ui", 0, 100, 1),
    "ui.dialog_close": ("ui", 0, 100, 1), "ui.menu_hover": ("ui", 0, 60, 1), "ui.menu_select": ("ui", 0, 200, 1),
    "card.pickup": ("sfx", 0, 40, 2), "card.play": ("sfx", 0, 60, 3), "card.land": ("sfx", 0, 50, 3), "land.play": ("sfx", 0, 60, 2),
    "card.draw": ("sfx", 0, 50, 3), "card.discard": ("sfx", 0, 60, 2), "deck.shuffle": ("sfx", 0, 300, 1), "tap": ("sfx", 0, 30, 3),
    "untap": ("sfx", 0, 30, 2), "flurry": ("sfx", 0, 500, 1), "spell.cast": ("sfx", 0, 80, 2), "ability.trigger": ("sfx", 0, 60, 3),
    "spell.resolve": ("sfx", 0, 80, 2), "spell.fizzle": ("sfx", 0, 100, 1), "damage.creature": ("sfx", 0, 40, 3),
    "damage.player": ("sfx", 0, 80, 2, {"music": -6, "ambience": -6}), "life.gain": ("sfx", 0, 120, 1), "life.loss": ("sfx", 0, 120, 1),
    "counter.add": ("sfx", 0, 40, 2), "counter.remove": ("sfx", 0, 40, 2), "destroy": ("sfx", 0, 60, 3), "exile": ("sfx", 0, 60, 2),
    "token.create": ("sfx", 0, 40, 2), "combat.attack": ("sfx", 0, 200, 1), "combat.block": ("sfx", 0, 150, 1),
    "phase.turn_mine": ("stinger", 0, 1000, 1, {"sfx": -4, "music": -4}), "phase.turn_theirs": ("stinger", 0, 1000, 1),
    "game.win": ("stinger", 0, 0, 1, {"sfx": -12, "ambience": -10}), "game.lose": ("stinger", 0, 0, 1, {"sfx": -12, "ambience": -10}),
    "rewind": ("stinger", 0, 0, 1), "vs.reveal": ("stinger", 0, 500, 1, {"music": -8}), "boot.splash": ("stinger", 0, 1000, 1),
}


def settings_for(cid):
    """A typed cue (card.land.artifact, land.play.forest, destroy.enchantment...) plays by its parent's rules."""
    while cid not in CUE_SETTINGS and "." in cid:
        cid = cid.rsplit(".", 1)[0]
    return CUE_SETTINGS[cid]


def parent_of(cid, cues):
    """The cue a typed cue falls back to (its synth recipe and haptic come from there), or None."""
    while "." in cid:
        cid = cid.rsplit(".", 1)[0]
        if cid in cues:
            return cid
    return None


def referenced_sources(sources):
    """Every source a recipe in this file names - the credits, whatever --only built."""
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        text = f.read()
    return {k for k in sources if re.search(r"[\"']" + re.escape(k) + r"[:\"']", text)}


FILE_NAMES = {"token.create": "spawn"}      # backup.py treats any file with "token" in its name as a possible secret and skips it

TIGHT = {"ui.hover", "ui.click", "ui.dialog_open", "ui.dialog_close", "card.pickup", "card.discard", "card.play"}   # AU1's recipes only:
                                                        # AU2's trim their own onsets (recipes_au2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "manticore_sound_sources"))
    ap.add_argument("--only", nargs="*", help="build just these cues (the manifest is still rewritten in full)")
    ap.add_argument("--no-music", action="store_true", help="skip the music tracks (they are the slow part)")
    a = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed on the PATH")
    with open(SOURCES, encoding="utf-8") as f:
        sources = json.load(f)["sources"]
    ctx = {"sources": sources, "cache": a.cache, "used": set()}
    L = lambda ref, raw=False: load(ref, ctx, raw)       # noqa: E731
    with open(os.path.join(SOUNDS, "cues.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    cues = manifest["cues"]
    want = set(a.only or [])

    def wanted(cid):
        return not want or cid in want

    au2 = recipes_au2(L)
    for cid, make in list(recipes(L).items()) + list(au2.items()):
        s = settings_for(cid)
        files = list(cues.get(cid, {}).get("files", []))
        if wanted(cid):
            variants = make()
            if cid in au2:                                 # AU2: as the candidates were built (their own onset trims)
                if s[0] in ("sfx", "ui") and s[2] < 100:
                    cap = int(AU2_CAP_SECONDS * SR)
                    variants = [fade(v[:cap], 0.0, 0.18) if len(v) > cap else v for v in variants]
            elif cid in TIGHT:                             # feedback sounds must answer the click at once: no slow lead-in
                variants = [fade(head(v, -18, 0.002), 0.002, 0.0) for v in variants]
            files = []
            stem = FILE_NAMES.get(cid, cid)
            for k, v in enumerate(variants):
                name = f"sfx/{stem}.{k + 1}.ogg"
                write_ogg(v, os.path.join(SOUNDS, name), 5)
                files.append(name)
            for fn in os.listdir(os.path.join(SOUNDS, "sfx")):          # fewer variants than before: the old extra files go
                if re.fullmatch(re.escape(stem) + r"\.\d+\.ogg", fn) and f"sfx/{fn}" not in files:
                    os.remove(os.path.join(SOUNDS, "sfx", fn))
            print(f"{cid:22s} {len(variants)} x {np.mean([len(v) for v in variants]) / SR:4.2f}s  {loudness(variants[0]):6.1f} LUFS")
        spec = {"bus": s[0], "gain_db": s[1], "files": files, "cooldown_ms": s[2], "max_voices": s[3]}
        if len(s) > 4:
            spec["duck"] = s[4]
        old = cues.get(cid) or cues.get(parent_of(cid, cues) or "", {})
        if "synth" in old:
            spec["synth"] = old["synth"]                  # still the fallback if a file is ever missing
        if "haptic" in old:
            spec["haptic"] = old["haptic"]
        cues[cid] = spec
    for cid, make in list(ambiences(L).items()) + list(ambiences_au2(L).items()):
        if wanted(cid):
            x = make()
            write_ogg(x, os.path.join(SOUNDS, f"ambience/{cid[4:]}.ogg"), 3)
            print(f"{cid:22s} loop {len(x) / SR:5.1f}s {loudness(x):6.1f} LUFS")
        cues[cid] = {"bus": "ambience", "gain_db": 0, "loop": True, "stream": True, "files": [f"ambience/{cid[4:]}.ogg"]}
    for cid, (ref, start, length, fo) in MUSIC.items():
        if wanted(cid) and not a.no_music:
            x = cut(L(ref), start, length)
            x = fade(x, 0.01, fo)
            x = level(x, MUSIC_LUFS, -1.0)
            write_ogg(x, os.path.join(SOUNDS, f"music/{cid[6:]}.ogg"), MUSIC_QUALITY.get(cid, 4))
            print(f"{cid:22s} {len(x) / SR:6.1f}s")
        cues[cid] = {"bus": "music", "gain_db": 0, "stream": True, "files": [f"music/{cid[6:]}.ogg"]}
    manifest["version"] = 3
    manifest["_comment"] = ("Every cue plays real recordings (CC0, built by tools/build_sounds.py from tools/sound_sources.json); 'synth' is "
                            "only the fallback if a file is missing. 'stream' cues (ambience, music) are played by the director's ambience/music "
                            "players, not as one-shots. AU2 (patch 49): the palette in sounds/SOUND_BIBLE.md; a typed cue (card.land.artifact, "
                            "land.play.forest, destroy.enchantment...) is chosen by cue_types.py and falls back to its parent when the card's "
                            "type isn't known or the typed cue doesn't exist. Levels (mean dB of the files): UI tick -45..-35, feedback "
                            "-37..-30, state change -26..-22, combat and player damage -20..-17, stingers -24..-18.")
    with open(os.path.join(SOUNDS, "cues.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    write_credits(sources, referenced_sources(sources))
    return 0


def write_credits(sources, used):
    lines = ["Sounds and music in this folder are built by tools/build_sounds.py from these recordings. Every one is CC0 1.0",
             "(public domain dedication: no permission or credit needed). We credit them anyway, with thanks.", ""]
    for key in sorted(used):
        s = sources[key]
        extra = f" - {s['note']}" if s.get("note") else ""
        title = f" \"{s['title']}\"" if s.get("title") else ""
        lines.append(f"{key}:{title} by {s['author']} - {s['page']} - CC0 1.0{extra}")
    with open(os.path.join(SOUNDS, "CREDITS.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
