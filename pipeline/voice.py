"""Per-scene narration with Gemini TTS. Per-scene audio gives exact timings for cuts and captions."""
from __future__ import annotations

import logging
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


def narrate(scenes, workdir: Path, mock: bool = False) -> tuple[Path, list[dict]]:
    """Returns (narration.wav, timeline) — timeline has start/end/duration for each scene."""
    vc = cfg()["voice"]
    adir = workdir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    gap = float(vc["scene_gap_seconds"])
    gap_wav = adir / "gap.wav"
    _silence(gap_wav, gap)

    parts, timeline, t = [], [], 0.0
    for s in scenes:
        wav = adir / f"scene_{s.id:02d}.wav"
        if not wav.exists():
            log.info("TTS scene %d/%d (%d words)", s.id, len(scenes), len(s.narration.split()))
            if mock:
                _mock_voice(wav, s.narration)
            else:
                style = vc["style"]
                if s.pronunciation:
                    style += f". Pronunciation: {s.pronunciation}"
                wav.write_bytes(gemini.tts(s.narration, vc["name"], style))
                time.sleep(1.0)  # be gentle with free-tier rate limits
        # normalise every clip to the same format so concat is sample-accurate
        norm = adir / f"scene_{s.id:02d}_n.wav"
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


def _mock_voice(path: Path, text: str):
    """Offline stand-in: a quiet tone lasting roughly as long as the text would take to speak (150 wpm)."""
    secs = max(1.5, len(text.split()) / 2.5)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}",
         "-af", "volume=0.05", "-ar", "24000", "-ac", "1", str(path)],
        check=True,
    )
