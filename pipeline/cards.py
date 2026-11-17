"""Branded image cards for Facebook: a 4:5 feed "did you know" card and a 9:16 Story teaser."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import BODY_FONT, BODY_FONT_BOLD, TITLE_FONT, cfg

ACCENT = (255, 199, 44)


def _background(per_scene, video: Path, out_dir: Path) -> Image.Image:
    """Best still from the Short: a real image asset first, otherwise a frame from the video."""
    for scene in per_scene:
        for a in scene:
            if a.get("type") == "image" and a.get("source") not in ("card",) and Path(a["path"]).exists():
                return ImageOps.exif_transpose(Image.open(a["path"])).convert("RGB")
    frame = out_dir / "fb_frame.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(video), "-frames:v", "1", str(frame)], check=True)
    return Image.open(frame).convert("RGB")


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, font_path, max_w, max_lines, start, minimum):
    size = start
    while size > minimum:
        font = ImageFont.truetype(str(font_path), size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4
    font = ImageFont.truetype(str(font_path), minimum)
    return font, _wrap(draw, text, font, max_w)[:max_lines]


def _base(per_scene, video, out: Path, size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = ImageOps.fit(_background(per_scene, video, out.parent), size, centering=(0.5, 0.4))
    img = ImageEnhance.Contrast(img).enhance(1.12)
    # dark gradient from the bottom so text always reads
    shade = Image.new("L", size, 0)
    d = ImageDraw.Draw(shade)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=int(235 * max(0.0, (y / h - 0.25) / 0.75) ** 1.2))
    img = Image.composite(Image.new("RGB", size, (6, 8, 12)), img, shade)
    top = Image.new("L", size, 0)
    ImageDraw.Draw(top).rectangle([0, 0, w, int(h * 0.16)], fill=120)
    return Image.composite(Image.new("RGB", size, (0, 0, 0)), img, top.filter(ImageFilter.GaussianBlur(60)))


def _brand(draw, x, y):
    f = ImageFont.truetype(str(BODY_FONT_BOLD), 34)
    label = cfg()["channel"]["name"].upper()
    tw = draw.textlength(label, font=f)
    draw.rounded_rectangle([x, y, x + tw + 48, y + 62], radius=31, fill=ACCENT)
    draw.text((x + 24, y + 11), label, font=f, fill=(0, 0, 0))


def feed_card(per_scene, video: Path, headline: str, fact: str, out: Path) -> Path:
    W, H = 1080, 1350
    img = _base(per_scene, video, out, (W, H))
    d = ImageDraw.Draw(img)
    _brand(d, 60, 60)
    pad = 70
    ff, flines = _fit(d, fact, BODY_FONT, W - 2 * pad, 5, 46, 34)
    hf, hlines = _fit(d, headline.upper(), TITLE_FONT, W - 2 * pad, 3, 120, 72)
    fact_h = len(flines) * int(ff.size * 1.32)
    head_h = len(hlines) * int(hf.size * 1.05)
    y = H - pad - fact_h - (int(hf.size * 0.18) + 58) - head_h - 60
    label = ImageFont.truetype(str(BODY_FONT_BOLD), 32)
    d.text((pad, y), "DID YOU KNOW?", font=label, fill=ACCENT)
    y += 60
    for i, line in enumerate(hlines):
        d.text((pad, y), line, font=hf, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
        y += int(hf.size * 1.05)
    y += int(hf.size * 0.18) + 18          # clear the headline's descenders + outline
    d.rectangle([pad, y, pad + 120, y + 6], fill=ACCENT)
    y += 34
    for line in flines:
        d.text((pad, y), line, font=ff, fill=(235, 235, 235))
        y += int(ff.size * 1.32)
    img.save(out, "JPEG", quality=90)
    return out


def story_card(per_scene, video: Path, teaser: str, title: str, out: Path) -> Path:
    W, H = 1080, 1920
    img = _base(per_scene, video, out, (W, H))
    d = ImageDraw.Draw(img)
    _brand(d, 60, 120)
    pad = 70
    tf, tlines = _fit(d, title.upper(), TITLE_FONT, W - 2 * pad, 4, 128, 80)
    small = ImageFont.truetype(str(BODY_FONT_BOLD), 40)
    y = H - 420 - len(tlines) * int(tf.size * 1.05)
    d.text((pad, y - 70), teaser.upper(), font=small, fill=ACCENT)
    for line in tlines:
        d.text((pad, y), line, font=tf, fill=(255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0))
        y += int(tf.size * 1.05)
    cta = "WATCH THE REEL  ▶"
    cw = d.textlength(cta, font=small)
    d.rounded_rectangle([pad, H - 300, pad + cw + 80, H - 200], radius=50, fill=(255, 255, 255))
    d.text((pad + 40, H - 278), cta, font=small, fill=(0, 0, 0))
    img.save(out, "JPEG", quality=90)
    return out
