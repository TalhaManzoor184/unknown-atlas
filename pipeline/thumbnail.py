"""1280x720 thumbnail: strong background, one short readable line, consistent brand system."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import BODY_FONT_BOLD, TITLE_FONT, cfg

log = logging.getLogger(__name__)
W, H = 1280, 720
ACCENT = (255, 199, 44)   # brand amber


def make_thumbnail(background: str, text: str, out: Path) -> Path:
    img = ImageOps.fit(ImageOps.exif_transpose(Image.open(background)).convert("RGB"), (W, H), centering=(0.5, 0.4))
    img = ImageEnhance.Contrast(img).enhance(1.18)
    img = ImageEnhance.Color(img).enhance(1.1)

    # left-side darkening so text always reads, plus vignette
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    for x in range(W):
        d.line([(x, 0), (x, H)], fill=int(200 * max(0, 1 - x / (W * 0.72))))
    vign = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vign).ellipse([-W * 0.2, -H * 0.3, W * 1.2, H * 1.3], fill=255)
    vign = ImageOps.invert(vign.filter(ImageFilter.GaussianBlur(120)))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(dark, img, shade)
    img = Image.composite(dark, img, vign.point(lambda p: int(p * 0.7)))

    words = text.upper().split()[:5]
    lines = _wrap(words, max_chars=11)
    draw = ImageDraw.Draw(img)
    size = 150 if len(lines) <= 2 else 118
    font = ImageFont.truetype(str(TITLE_FONT), size)
    y = (H - len(lines) * int(size * 1.05)) // 2
    for i, line in enumerate(lines):
        fill = ACCENT if i == len(lines) - 1 else (255, 255, 255)
        draw.text((64, y), line, font=font, fill=fill, stroke_width=8, stroke_fill=(0, 0, 0))
        y += int(size * 1.05)

    # brand tab bottom-left
    bf = ImageFont.truetype(str(BODY_FONT_BOLD), 30)
    label = cfg()["channel"]["name"].upper()
    tw = draw.textlength(label, font=bf)
    draw.rectangle([0, H - 70, tw + 72, H - 20], fill=ACCENT)
    draw.text((36, H - 66), label, font=bf, fill=(0, 0, 0))

    img.save(out, "JPEG", quality=90, optimize=True)
    if out.stat().st_size > 2_000_000:  # YouTube limit is 2 MB
        img.save(out, "JPEG", quality=78, optimize=True)
    return out


def _wrap(words: list[str], max_chars: int) -> list[str]:
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]
