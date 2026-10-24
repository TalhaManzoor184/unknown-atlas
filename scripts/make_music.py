"""Generates original, royalty-free ambient documentary beds into assets/music/.

Everything is synthesized from scratch (no samples), so there is nothing to license and no Content ID risk.

    python scripts/make_music.py            # writes 4 tracks (~4 min each) as .mp3
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "music"
A4 = 440.0
NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}


def hz(name: str) -> float:
    pitch, octave = name[:-1], int(name[-1])
    return A4 * 2 ** ((NOTE[pitch] - 9) / 12 + (octave - 4))


def pad_voice(freq: float, n: int, rng, bright: float) -> np.ndarray:
    """Warm detuned pad: 3 slightly detuned oscillators with soft harmonics and slow vibrato."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for detune in (-0.12, 0.0, 0.11):
        f = freq * 2 ** (detune / 12)
        vib = 1 + 0.0015 * np.sin(2 * np.pi * rng.uniform(0.08, 0.2) * t + rng.uniform(0, 6.28))
        phase = 2 * np.pi * np.cumsum(f * vib) / SR
        for h in range(1, 6):
            out += (bright ** (h - 1)) / h * np.sin(h * phase + rng.uniform(0, 6.28))
    return out / 3


def envelope(n: int, attack: float, release: float) -> np.ndarray:
    a, r = int(attack * SR), int(release * SR)
    env = np.ones(n)
    env[:a] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a))
    env[n - r :] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, r))
    return env


def chord_bed(chords: list[list[str]], seconds_each: float, repeats: int, rng, bright=0.35) -> np.ndarray:
    seg = int(seconds_each * SR)
    overlap = int(3.0 * SR)
    total = seg * len(chords) * repeats + overlap
    bed = np.zeros(total)
    pos = 0
    for _ in range(repeats):
        for ch in chords:
            n = seg + overlap
            s = sum(pad_voice(hz(x), n, rng, bright) for x in ch) / len(ch)
            bed[pos : pos + n] += s * envelope(n, 3.0, 3.0)
            pos += seg
    return bed


def drone(note: str, n: int, rng) -> np.ndarray:
    t = np.arange(n) / SR
    f = hz(note)
    swell = 0.75 + 0.25 * np.sin(2 * np.pi * t / 23 + rng.uniform(0, 6.28))
    return (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t)) * swell


def air(n: int, rng) -> np.ndarray:
    """Soft filtered noise, like distant wind."""
    from scipy.signal import lfilter

    w = rng.standard_normal(n)
    alpha = 0.002  # one-pole lowpass
    b = lfilter([alpha], [1, -(1 - alpha)], w)
    t = np.arange(n) / SR
    return b * (0.6 + 0.4 * np.sin(2 * np.pi * t / 17)) * 8


def bells(notes: list[str], n: int, rng, every: tuple[float, float]) -> np.ndarray:
    out = np.zeros(n)
    t0 = rng.uniform(*every)
    while t0 * SR < n - SR * 6:
        f = hz(rng.choice(notes))
        k = int(6 * SR)
        t = np.arange(k) / SR
        tone = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2.01 * f * t) + 0.12 * np.sin(2 * np.pi * 3.98 * f * t))
        tone *= np.exp(-t * 0.9) * (1 - np.exp(-t * 60))
        s = int(t0 * SR)
        out[s : s + k] += tone * rng.uniform(0.5, 1.0)
        t0 += rng.uniform(*every)
    return out


def pulse(note: str, bpm: float, n: int) -> np.ndarray:
    """Slow heartbeat-like low pulse for tension."""
    out = np.zeros(n)
    beat = 60 / bpm
    k = int(0.7 * SR)
    t = np.arange(k) / SR
    hit = np.sin(2 * np.pi * hz(note) * t) * np.exp(-t * 6)
    pos = 0.0
    i = 0
    while int(pos * SR) + k < n:
        s = int(pos * SR)
        out[s : s + k] += hit * (1.0 if i % 2 == 0 else 0.6)
        pos += beat * (0.42 if i % 2 == 0 else 1.58)
        i += 1
    return out


def stereo(mono_l: np.ndarray, rng) -> np.ndarray:
    d = int(0.011 * SR)  # small Haas delay for width
    r = np.concatenate([np.zeros(d), mono_l[:-d]])
    return np.stack([mono_l, 0.85 * r + 0.15 * mono_l], axis=1)


def save(name: str, audio: np.ndarray):
    audio = audio / (np.max(np.abs(audio)) + 1e-9) * 0.8
    fade = int(2.5 * SR)
    audio[:fade] *= np.linspace(0, 1, fade)[:, None]
    audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "x.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((audio * 32767).astype("<i2").tobytes())
        # gentle lowpass + roomy reverb + consistent loudness
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
            "-af", "lowpass=f=5200,aecho=0.8:0.75:90|170|260:0.35|0.25|0.18,loudnorm=I=-20:TP=-2:LRA=9",
            "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "160k", str(OUT / f"{name}.mp3"),
        ], check=True)
    print("wrote", OUT / f"{name}.mp3")


def main():
    rng = np.random.default_rng(1959)

    # 1. "Unknown" — dark minor drift (mysteries)
    chords = [["A2", "E3", "C4"], ["F2", "C3", "A3"], ["D2", "A2", "F3"], ["E2", "B2", "G#3"]]
    bed = chord_bed(chords, 12, 5, rng, bright=0.3)
    n = len(bed)
    mix = 0.55 * bed + 0.35 * drone("A1", n, rng) + 0.25 * air(n, rng) + 0.08 * bells(["A4", "C5", "E5"], n, rng, (9, 16))
    save("atlas-unknown", stereo(mix, rng))

    # 2. "Discovery" — warmer, hopeful (science / solved mysteries)
    chords = [["C3", "G3", "E4"], ["A2", "E3", "C4"], ["F2", "C3", "A3"], ["G2", "D3", "B3"]]
    bed = chord_bed(chords, 11, 5, rng, bright=0.42)
    n = len(bed)
    mix = 0.6 * bed + 0.25 * drone("C2", n, rng) + 0.15 * air(n, rng) + 0.12 * bells(["E5", "G5", "C5", "D5"], n, rng, (5, 9))
    save("atlas-discovery", stereo(mix, rng))

    # 3. "Tension" — low pulse under a suspended minor pad (investigations, twists)
    chords = [["D2", "A2", "F3"], ["D2", "A2", "G3"], ["C2", "G2", "D#3"], ["D2", "A2", "E3"]]
    bed = chord_bed(chords, 13, 5, rng, bright=0.25)
    n = len(bed)
    mix = 0.5 * bed + 0.3 * drone("D1", n, rng) + 0.2 * air(n, rng) + 0.22 * pulse("D2", 52, n)
    save("atlas-tension", stereo(mix, rng))

    # 4. "Deep Field" — airy, shimmering (space, strange places)
    chords = [["E2", "B2", "F#3", "G#3"], ["C#2", "G#2", "E3", "B3"], ["A1", "E2", "C#3", "G#3"], ["B1", "F#2", "D#3", "A3"]]
    bed = chord_bed(chords, 14, 4, rng, bright=0.5)
    n = len(bed)
    mix = 0.55 * bed + 0.2 * drone("E1", n, rng) + 0.2 * air(n, rng) + 0.14 * bells(["B5", "E5", "F#5", "G#5"], n, rng, (4, 8))
    save("atlas-deep-field", stereo(mix, rng))


if __name__ == "__main__":
    sys.exit(main())
