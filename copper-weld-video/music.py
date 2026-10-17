"""Original upbeat 'positive' ukulele-pop loop (C - G - Am - F, 120 BPM), 32 s.

Ukulele = Karplus-Strong strums, plus snaps, whistle lead, glockenspiel, bass, kick, claps.
Writes data/music.wav (44.1 kHz stereo) and data/spectrum.json (bar heights per video frame).
"""
import json
import wave

import numpy as np

SR = 44100
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 32.0
N = int(SR * DUR)
rng = np.random.default_rng(7)
L = np.zeros(N)
R = np.zeros(N)


def hz(note):
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return 440 * 2 ** ((names[note[:-1]] + 12 * (int(note[-1]) + 1) - 69) / 12)


def add(sig, t, gain=1.0, pan=0.0):
    i = int(t * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    L[i:i + len(sig)] += sig * (1 - pan) ** 0.5
    R[i:i + len(sig)] += sig * (1 + pan) ** 0.5


_ks = {}


def pluck(f, dur=1.2):
    if f not in _ks:
        p = int(SR / f)
        n = int(dur * SR)
        y = np.zeros(n + p)
        y[:p] = rng.uniform(-1, 1, p)
        y[:p] = np.convolve(y[:p], [0.5, 0.5], "same")    # softer nylon attack
        for s in range(p, n + p, p):
            prev = y[s - p:s]
            blk = 0.996 * 0.5 * (prev + np.roll(prev, -1))
            y[s:s + p] = blk[: len(y[s:s + p])]
        _ks[f] = y[p:] * np.exp(-np.linspace(0, 3.5, n))
    return _ks[f]


UKE = {"C": ["G4", "C4", "E4", "C5"], "G": ["G4", "D4", "G4", "B4"],
       "Am": ["A4", "C4", "E4", "A4"], "F": ["A4", "C4", "F4", "A4"]}
PROG = ["C", "G", "Am", "F"]
BASS = {"C": "C2", "G": "G2", "Am": "A2", "F": "F2"}


def strum(chord, t, down=True, g=0.22):
    strings = UKE[chord] if down else UKE[chord][::-1]
    for k, n in enumerate(strings):
        add(pluck(hz(n)), t + k * 0.012, g * (1 if down else 0.7), pan=-0.25)


def env(n, a=0.005, d=0.2):
    t = np.arange(n) / SR
    return np.minimum(t / a, 1) * np.exp(-t / d)


def kick(t):
    n = int(0.35 * SR)
    tt = np.arange(n) / SR
    f = 50 + 90 * np.exp(-tt * 30)
    add(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.002, 0.12), t, 0.55)


def clap(t, g=0.18):
    n = int(0.2 * SR)
    noise = rng.uniform(-1, 1, n)
    noise = noise - np.convolve(noise, np.ones(4) / 4, "same")   # crude high-pass
    e = env(n, 0.001, 0.05)
    for off in (0, 0.008, 0.016):
        add(noise * e, t + off, g / 2, pan=0.1)


def snap(t):
    n = int(0.06 * SR)
    noise = rng.uniform(-1, 1, n)
    noise = noise - np.convolve(noise, np.ones(3) / 3, "same")
    add(noise * env(n, 0.0005, 0.012), t, 0.35, pan=0.35)


def hat(t, g=0.05):
    n = int(0.05 * SR)
    noise = rng.uniform(-1, 1, n)
    noise = noise - np.convolve(noise, np.ones(2) / 2, "same")
    add(noise * env(n, 0.0005, 0.015), t, g, pan=0.4)


def bass(note, t, dur):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    f = hz(note)
    s = np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)
    add(s * env(n, 0.01, 0.35), t, 0.28)


def whistle(note, t, dur, g=0.16):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    f = hz(note) * (1 + 0.006 * np.sin(2 * np.pi * 5.5 * tt) * np.minimum(tt / 0.15, 1))
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.04 * rng.uniform(-1, 1, n)
    e = np.minimum(tt / 0.03, 1) * np.minimum((dur - tt) / 0.05, 1).clip(0)
    add(s * e, t, g, pan=0.15)


def glock(note, t, g=0.10):
    n = int(1.2 * SR)
    tt = np.arange(n) / SR
    f = hz(note)
    s = np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * f * 2.76 * tt) * np.exp(-tt * 8)
    add(s * env(n, 0.001, 0.4), t, g, pan=-0.4)


# whistle melody: (beat offset within 2-bar phrase, note, beats)
MEL_A = [(0, "E5", 1), (1, "G5", 1), (2, "C6", 1.5), (3.5, "B5", 0.5), (4, "G5", 1.5), (5.5, "A5", 0.5), (6, "G5", 2)]
MEL_B = [(0, "E5", 1), (1, "D5", 0.5), (1.5, "E5", 0.5), (2, "C5", 1), (3, "A4", 1), (4, "C5", 1), (5, "D5", 1), (6, "C5", 2)]
GLK = ["C6", "E6", "G6", "E6", "D6", "G6", "B6", "G6", "C6", "E6", "A6", "E6", "C6", "F6", "A6", "F6"]

bars = int(DUR / BAR)
for b in range(bars):
    t0 = b * BAR
    ch = PROG[b % 4]
    last = b == bars - 1
    if last:
        strum("C", t0, g=0.3)
        bass("C2", t0, 1.5)
        kick(t0)
        glock("C6", t0, 0.14)
        glock("G6", t0 + 0.02, 0.1)
        whistle("C6", t0, 1.6)
        continue
    # ukulele: D . D U . U D U
    for beat8, down in [(0, 1), (2, 1), (3, 0), (5, 0), (6, 1), (7, 0)]:
        strum(ch, t0 + beat8 * BEAT / 2, bool(down))
    if b < 2:                               # intro: uke + snaps only
        for q in (1, 3):
            snap(t0 + q * BEAT)
        continue
    bass(BASS[ch], t0, BEAT * 1.5)
    bass(BASS[ch], t0 + 2.5 * BEAT, BEAT)
    for q in range(4):
        if q % 2 == 0:
            kick(t0 + q * BEAT)
        else:
            clap(t0 + q * BEAT)
        hat(t0 + q * BEAT + BEAT / 2)
    if 2 <= b < 10:
        mel = MEL_A if (b // 2) % 2 else MEL_B
        if b % 2 == 0:
            for off, n, d in mel:
                whistle(n, t0 + off * BEAT, d * BEAT * 0.95)
    if b >= 10:
        for q in range(4):
            glock(GLK[(b % 4) * 4 + q], t0 + q * BEAT)
        for q in range(4):
            snap(t0 + q * BEAT + BEAT * 0.75)

mix = np.stack([L, R], 1)
# soft limiter + fades
mix = np.tanh(mix * 1.4) / np.tanh(1.4)
fade_in = int(0.05 * SR)
mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
fade_out = int(1.2 * SR)
mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
mix /= np.abs(mix).max() / 0.9

with wave.open("data/music.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype(np.int16).tobytes())

# spectrum bars per 30fps frame for the on-screen visualiser
FPS, BANDS = 30, 48
mono = mix.mean(1)
win = 2048
edges = np.geomspace(60, 12000, BANDS + 1)
freqs = np.fft.rfftfreq(win, 1 / SR)
spec = []
for fi in range(int(DUR * FPS)):
    c = int(fi / FPS * SR)
    seg = mono[max(0, c - win // 2): c + win // 2]
    seg = np.pad(seg, (0, win - len(seg))) * np.hanning(win)
    mag = np.abs(np.fft.rfft(seg))
    bands = [mag[(freqs >= lo) & (freqs < hi)].mean() if ((freqs >= lo) & (freqs < hi)).any() else 0
             for lo, hi in zip(edges[:-1], edges[1:])]
    spec.append(bands)
spec = np.array(spec)
spec = np.log1p(spec * 4) / np.log1p(spec * 4).max()
json.dump(np.round(spec, 3).tolist(), open("data/spectrum.json", "w"))
print("ok", mix.shape)
