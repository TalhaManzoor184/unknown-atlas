"""Loads config.yaml and environment overrides."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
STATE_FILE = ROOT / "state" / "published.json"
TOPICS_FILE = ROOT / "content" / "topics.yaml"
FONTS_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"

TITLE_FONT = FONTS_DIR / "Anton-Regular.ttf"
BODY_FONT = FONTS_DIR / "Montserrat-SemiBold.ttf"
BODY_FONT_BOLD = FONTS_DIR / "Montserrat-Bold.ttf"


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    m = data["models"]
    m["text"] = os.getenv("GEMINI_TEXT_MODEL") or m["text"]
    m["tts"] = os.getenv("GEMINI_TTS_MODEL") or m["tts"]
    m["image"] = os.getenv("GEMINI_IMAGE_MODEL") or m["image"]
    p = data["publishing"]
    p["mode"] = (os.getenv("PUBLISH_MODE") or p["mode"]).strip().lower()
    if os.getenv("VOICE_NAME"):
        data["voice"]["name"] = os.environ["VOICE_NAME"]
    if os.getenv("AI_IMAGES", "").lower() in {"0", "false", "no", "off"}:
        data["visuals"]["ai_images"] = False
    return data


def env(name: str, required: bool = True) -> str:
    val = os.getenv(name, "").strip()
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val
