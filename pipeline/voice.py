"""Narration with Gemini TTS.

TTS quotas are small (requests per minute/day), so scenes are grouped into a few chunks of ~350 words
and each chunk is one TTS request. Each chunk is then split back into per-scene clips at the natural
pauses between paragraphs (ffmpeg silencedetect), so cuts and captions stay in sync with the voice.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
import wave
from pathlib import Path

from . import gemini
from .config import cfg

log = logging.getLogger(__name__)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _silence(path: Path, seconds: float, rate: int = 24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def _chunks(scenes, max_words: int) -> list[list]:
    groups, cur, n = [], [], 0
    for s in scenes:
        w = len(s.narration.split())
        if cur and n + w > max_words:
            groups.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        groups.append(cur)
    return groups


def _silences(path: Path) -> list[float]:
    """Midpoints of pauses >= 0.3 s."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "silencedetect=noise=-38dB:d=0.3", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(a + b) / 2 for a, b in zip(starts, ends)]


def _split_points(group, total: float, pauses: list[float]) -> list[float]:
    """Pick one pause near each expected scene boundary (expected = proportional to characters)."""
    chars = [len(s.narration) + 1 for s in group]
    whole = sum(chars)
    points, acc, last = [], 0, 0.0
    for c in chars[:-1]:
        acc += c
        guess = total * acc / whole
        window = max(1.5, total * 0.08)
        near = [p for p in pauses if p > last + 0.5 and abs(p - guess) <= window]
        best = min(near, key=lambda p: abs(p - guess)) if near else guess
        if not near:
            log.warning("No pause found near %.1fs — using estimated scene boundary", guess)
        points.append(best)
        last = best
    return points


_tts_models: list[str] = []


def _tts_with_patience(text: str, voice: str, style: str) -> bytes:
    """Free-tier TTS is limited per minute AND per day (10/day per model at the time of writing).
    Per-minute limit -> wait for the window. Daily limit -> move to the next TTS model (each has its own quota)."""
    if not _tts_models:
        m = cfg()["models"]
        _tts_models.extend([m["tts"], *[x for x in m.get("tts_fallbacks", []) if x != m["tts"]]])
    waits = 0
    while _tts_models:
        model = _tts_models[0]
        try:
            return gemini.tts(text, voice, style, model=model)
        except gemini.QuotaError as e:
            msg = str(e).lower()
            if "per day" in msg or "requests per day" in msg or waits >= 4:
                log.warning("Daily TTS quota used up on %s — trying next TTS model", model)
                _tts_models.pop(0)
                waits = 0
                continue
            waits += 1
            log.warning("TTS per-minute limit on %s — waiting 65s (%d/4)", model, waits)
            time.sleep(65)
    raise RuntimeError(
        "All Gemini TTS models are out of quota for today (free tier). The next scheduled run will continue, "
        "or enable billing on the Gemini API key for higher limits."
    )


def narrate(scenes, workdir: Path, mock: bool = False) -> tuple[Path, list[dict]]:
    """Returns (narration.wav, timeline) — timeline has start/end/duration for each scene."""
    vc = cfg()["voice"]
    adir = workdir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    gap = float(vc["scene_gap_seconds"])
    gap_wav = adir / "gap.wav"
    _silence(gap_wav, gap)

    groups = _chunks(scenes, int(vc.get("chunk_words", 350)))
    log.info("TTS: %d scenes in %d request(s)", len(scenes), len(groups))
    for gi, group in enumerate(groups, 1):
        if all((adir / f"scene_{s.id:02d}.wav").exists() for s in group):
            continue
        chunk = adir / f"chunk_{gi:02d}.wav"
        if not chunk.exists():
            text = "\n\n".join(s.narration for s in group)
            log.info("TTS chunk %d/%d (%d words, scenes %d-%d)", gi, len(groups), len(text.split()), group[0].id, group[-1].id)
            if mock:
                _mock_voice(chunk, group)
            else:
                style = vc["style"] + ". Pause briefly between paragraphs."
                pron = "; ".join(s.pronunciation for s in group if s.pronunciation)
                if pron:
                    style += f" Pronunciation: {pron}"
                chunk.write_bytes(_tts_with_patience(text, vc["name"], style))
        total = duration(chunk)
        cuts = [0.0, *_split_points(group, total, _silences(chunk)), total]
        for s, a, b in zip(group, cuts[:-1], cuts[1:]):
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(chunk), "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                 "-c:a", "pcm_s16le", str(adir / f"scene_{s.id:02d}.wav")],
                check=True,
            )

    parts, timeline, t = [], [], 0.0
    for s in scenes:
        wav = adir / f"scene_{s.id:02d}.wav"
        norm = adir / f"scene_{s.id:02d}_n.wav"
        # trim leading/trailing silence and normalise format so concat is sample-accurate
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
             "-ar", "24000", "-ac", "1", "-sample_fmt", "s16", str(norm)],
            check=True,
        )
        d = duration(norm)
        timeline.append({"id": s.id, "start": t, "end": t + d, "duration": d + gap})
        t += d + gap
        parts += [norm, gap_wav]

    lst = adir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    out = workdir / "narration.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    log.info("Narration: %.1fs across %d scenes", t, len(scenes))
    return out, timeline


def _mock_voice(path: Path, group):
    """Offline stand-in: a tone per scene (150 wpm) separated by 0.8 s pauses, like real paragraph breaks."""
    pieces = []
    for i, s in enumerate(group):
        secs = max(1.5, len(s.narration.split()) / 2.5)
        pieces.append(f"sine=frequency={200 + 40 * (i % 3)}:duration={secs}")
    inputs, filt = [], []
    for i, src in enumerate(pieces):
        inputs += ["-f", "lavfi", "-i", src]
        filt.append(f"[{i}:a]volume=0.3,apad=pad_dur=0.8[a{i}]")
    filt.append("".join(f"[a{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=0:a=1[o]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[o]",
                    "-ar", "24000", "-ac", "1", str(path)], check=True)
