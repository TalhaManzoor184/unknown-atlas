"""FFmpeg renderer: shots -> graded segments -> concat -> captions/overlays -> music mix."""
from __future__ import annotations

import hashlib
import logging
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from . import captions
from .config import FONTS_DIR, MUSIC_DIR, cfg

log = logging.getLogger(__name__)
FPS = 30


def _run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:8])}...\n{r.stderr[-2000:]}")


def _prep_image(src: str, w: int, h: int, out: Path) -> Path:
    """Cover-fit onto a 1.5x canvas with a blurred fill, so portrait/odd images never get badly cropped."""
    W, H = int(w * 1.5), int(h * 1.5)
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    bg = ImageOps.fit(img, (W, H)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.eval(bg, lambda p: int(p * 0.55))
    ratio = img.width / img.height
    target = W / H
    if abs(ratio - target) < 0.25:  # close enough: fill the frame
        fg = ImageOps.fit(img, (W, H))
        bg.paste(fg, (0, 0))
    else:
        fg = ImageOps.contain(img, (W, H))
        bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    bg.save(out, quality=93)
    return out


MOTIONS = [
    ("1+0.12*on/{N}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),        # slow push in
    ("1.12-0.12*on/{N}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),     # slow pull out
    ("1.12", "(iw-iw/zoom)*on/{N}", "ih/2-(ih/zoom/2)"),              # pan right
    ("1.12", "(iw-iw/zoom)*(1-on/{N})", "ih/2-(ih/zoom/2)"),          # pan left
]
GRADE = "eq=contrast=1.06:saturation=0.88:brightness=-0.02,vignette=PI/5"


def _segment(asset: dict, dur: float, w: int, h: int, out: Path, idx: int):
    n = max(2, round(dur * FPS))
    common = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    if asset["type"] == "video":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1,{GRADE},format=yuv420p"
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", asset["path"], "-t", f"{dur:.3f}", "-vf", vf, "-frames:v", str(n), *common, str(out)])
    else:
        prepped = _prep_image(asset["path"], w, h, out.with_suffix(".jpg"))
        z, x, y = (s.format(N=n) for s in MOTIONS[idx % len(MOTIONS)])
        vf = f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={FPS},setsar=1,{GRADE},format=yuv420p"
        _run(["ffmpeg", "-y", "-i", str(prepped), "-vf", vf, "-frames:v", str(n), *common, str(out)])


# ---------------- captions / overlays (ASS) ----------------

def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(scenes, timeline, w: int, h: int, portrait: bool, brand: str, path: Path):
    if portrait:
        cap = f"Style: Cap,Anton,108,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,6,2,2,80,80,{int(h*0.30)},1"
        title = f"Style: Title,Anton,78,&H0000D7FF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,5,0,8,80,80,{int(h*0.13)},1"
        brand_style = f"Style: Brand,Montserrat SemiBold,34,&H99FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,3,0,1,2,0,8,40,40,{int(h*0.06)},1"
        words_per = 3
    else:
        cap = "Style: Cap,Montserrat SemiBold,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,160,160,64,1"
        title = "Style: Title,Anton,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,4,0,7,90,90,80,1"
        brand_style = "Style: Brand,Montserrat SemiBold,26,&H99FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,3,0,1,1,0,9,48,48,40,1"
        words_per = 7

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        cap, title, brand_style, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    total = timeline[-1]["start"] + timeline[-1]["duration"] + 1.0
    lines.append(f"Dialogue: 0,{_ts(0.5)},{_ts(total - 0.5)},Brand,,0,0,0,,{_esc(brand.upper())}")

    for a, b, c in captions.cues(scenes, timeline, path.parent, words_per):
        text = c.upper() if portrait else c
        lines.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{_esc(text)}")
    for s, tl in zip(scenes, timeline):
        if s.on_screen_text.strip():
            end = min(tl["start"] + 3.8, tl["start"] + tl["duration"])
            ost = s.on_screen_text.strip().upper()
            lines.append(f"Dialogue: 2,{_ts(tl['start'] + 0.2)},{_ts(end)},Title,,0,0,0,,{{\\fad(300,400)}}{_esc(ost)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------- main render ----------------

def caption_selftest(workdir: Path):
    """Burn a test caption onto a black frame and make sure white pixels appear.
    Some ffmpeg builds ship libass without working fonts and silently render NO captions."""
    from PIL import Image

    test = workdir / "selftest.ass"
    test.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: T,Montserrat SemiBold,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,CAPTION TEST\n", encoding="utf-8")
    png = workdir / "selftest.png"
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=640x360:d=1", "-vf", f"ass='{test}':fontsdir='{FONTS_DIR}'",
          "-frames:v", "1", str(png)])
    if Image.open(png).convert("L").getextrema()[1] < 128:
        raise RuntimeError("Caption self-test failed: this ffmpeg build renders no subtitles (libass/fonts). "
                           "Install ffmpeg from apt (see workflow).")
    log.info("Caption self-test passed")


def render(scenes, timeline, assets_per_scene, narration: Path, workdir: Path, out: Path, portrait: bool, slug: str,
           pillar: str = ""):
    fmt = cfg()["shorts" if portrait else "long_form"]
    w, h = fmt["width"], fmt["height"]
    seg_dir = workdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    caption_selftest(workdir)
    min_shot = 2.0 if portrait else 2.8

    segs, idx = [], 0
    for si, (s, tl, assets) in enumerate(zip(scenes, timeline, assets_per_scene)):
        dur = tl["duration"] + (1.0 if si == len(scenes) - 1 else 0.0)  # tail on the final scene
        k = max(1, min(len(assets), int(dur // min_shot)))
        shots = assets[:k]
        per = dur / k
        for a in shots:
            seg = seg_dir / f"seg_{idx:03d}.mp4"
            if not seg.exists():
                _segment(a, per, w, h, seg, idx)
            segs.append(seg)
            idx += 1
    log.info("Rendered %d shot segments", len(segs))

    concat = seg_dir / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
    visual = workdir / "visual.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(visual)])

    ass = build_ass(scenes, timeline, w, h, portrait, cfg()["channel"]["name"], workdir / "captions.ass")
    total = timeline[-1]["start"] + timeline[-1]["duration"] + 1.0

    music = _pick_music(slug, pillar)
    vf = f"[0:v]ass='{ass}':fontsdir='{FONTS_DIR}',fade=t=in:st=0:d=0.6,fade=t=out:st={total - 1.2:.2f}:d=1.2[v]"
    cmd = ["ffmpeg", "-y", "-i", str(visual), "-i", str(narration)]
    if music:
        vol = cfg()["music"]["volume"]
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        af = (
            f"[1:a]aresample=48000,asplit=2[vo][sc];"
            f"[2:a]aresample=48000,volume={vol},afade=t=in:st=0:d=2,afade=t=out:st={total - 3:.2f}:d=3[mu];"
            f"[mu][sc]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=600[duck];"
            f"[vo][duck]amix=inputs=2:duration=first:normalize=0,apad,atrim=0:{total:.2f},loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        af = f"[1:a]aresample=48000,apad,atrim=0:{total:.2f},loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    cmd += [
        "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out),
    ]
    _run(cmd)
    visual.unlink(missing_ok=True)  # intermediate; keeps the artifact small
    log.info("Final video: %s (%.1fs)", out, total)
    return out, total


MOODS = {  # topic pillar -> preferred music beds (file name prefixes in assets/music)
    "mysteries": ["atlas-unknown", "atlas-tension"],
    "history": ["atlas-unknown", "atlas-tension", "atlas-discovery"],
    "science": ["atlas-discovery", "atlas-deep-field"],
    "places-space": ["atlas-deep-field", "atlas-discovery"],
}


def _pick_music(slug: str, pillar: str = "") -> Path | None:
    tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))
    if not tracks:
        log.info("No music in assets/music — rendering narration only")
        return None
    preferred = [t for t in tracks if any(t.stem.startswith(m) for m in MOODS.get(pillar, []))]
    pool = preferred or tracks
    rnd = random.Random(int(hashlib.md5(slug.encode()).hexdigest()[:8], 16))
    choice = rnd.choice(pool)
    log.info("Music: %s", choice.name)
    return choice
