"""Caption timing + SRT export.

Timing: each scene's narration is split into sentences, and sentence boundaries are snapped to the real
pauses in that scene's voice clip (ffmpeg silencedetect). Inside a sentence, words are spread by length.
This keeps captions within a fraction of a second of the voice without a speech-recognition model.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from . import voice


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])", text.strip())
    return [p for p in parts if p.strip()]


def sentence_spans(scene, start: float, end: float, wav: Path | None) -> list[tuple[str, float, float]]:
    sents = _sentences(scene.narration)
    total = max(0.1, end - start)
    if len(sents) > 1 and wav is not None and wav.exists():
        pauses = voice._silences(wav)
        cuts = voice._split_points([SimpleNamespace(narration=s) for s in sents], total, pauses, quiet=True)
    else:
        acc, cuts, whole = 0, [], sum(len(s) + 1 for s in sents)
        for s in sents[:-1]:
            acc += len(s) + 1
            cuts.append(total * acc / whole)
    bounds = [0.0, *cuts, total]
    return [(s, start + a, start + b) for s, a, b in zip(sents, bounds[:-1], bounds[1:])]


def cues(scenes, timeline, workdir: Path, words_per: int) -> list[tuple[float, float, str]]:
    """(start, end, text) caption cues of at most `words_per` words, never crossing a sentence."""
    out = []
    adir = workdir / "audio"
    for s, tl in zip(scenes, timeline):
        wav = adir / f"scene_{s.id:02d}_n.wav"
        for sent, a, b in sentence_spans(s, tl["start"], tl["end"], wav):
            words = sent.split()
            chunks = [" ".join(words[i : i + words_per]) for i in range(0, len(words), words_per)]
            whole = sum(len(c) + 1 for c in chunks) or 1
            t = a
            for c in chunks:
                d = (b - a) * (len(c) + 1) / whole
                out.append((t, t + d, c))
                t += d
    return out


def _srt_ts(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(scenes, timeline, workdir: Path, path: Path) -> Path:
    """Closed captions for YouTube (2-line style cues, ~12 words)."""
    lines = []
    for i, (a, b, text) in enumerate(cues(scenes, timeline, workdir, words_per=12), 1):
        lines += [str(i), f"{_srt_ts(a)} --> {_srt_ts(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
