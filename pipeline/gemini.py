"""Thin wrapper over the Gemini Interactions API (google-genai >= 2.25).

Everything that talks to Gemini goes through here so model names, retries and
response parsing live in one place.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .config import cfg, env

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_client = None


def client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=env("GEMINI_API_KEY"))
    return _client


def _get(obj: Any, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _call(**body):
    """Call interactions.create with retries on rate limits / transient errors."""
    delay = 8.0
    last: Exception | None = None
    for attempt in range(6):
        try:
            return client().interactions.create(**body)
        except Exception as e:  # SDK raises various error types; inspect message
            last = e
            msg = str(e).lower()
            model = body.get("model")
            quota = "quota" in msg or "too_many_requests" in msg or "resource_exhausted" in msg
            transient = quota or any(k in msg for k in ("429", "rate", "500", "503", "unavailable", "deadline", "timeout", "overloaded"))
            # Quota errors: retry only twice (covers per-minute limits); daily / not-on-free-tier limits won't clear.
            if not transient or attempt == 5 or (quota and attempt >= 2):
                tool = " + google_search" if body.get("tools") else ""
                raise RuntimeError(f"Gemini call failed [model={model}{tool}]: {e}") from e
            wait = 35.0 * (attempt + 1) if quota else delay
            log.warning("Gemini %s on %s; retry %d in %.0fs", "quota limit" if quota else "transient error", model, attempt + 1, wait)
            time.sleep(wait)
            delay = min(delay * 2, 120)
    raise last  # pragma: no cover


def _output_text(interaction) -> str:
    text = _get(interaction, "output_text")
    if text:
        return text
    parts = []
    for step in _get(interaction, "steps", []) or []:
        if _get(step, "type") == "model_output":
            for block in _get(step, "content", []) or []:
                if _get(block, "type") == "text":
                    parts.append(_get(block, "text", ""))
    return "\n".join(parts)


def citations(interaction) -> list[dict]:
    """Collect url_citation annotations (url, title) from a grounded interaction."""
    seen, out = set(), []
    for step in _get(interaction, "steps", []) or []:
        if _get(step, "type") != "model_output":
            continue
        for block in _get(step, "content", []) or []:
            for ann in _get(block, "annotations", []) or []:
                if _get(ann, "type") != "url_citation":
                    continue
                url = _get(ann, "url")
                if url and url not in seen:
                    seen.add(url)
                    out.append({"url": url, "title": _get(ann, "title") or ""})
    return out


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.S)
    if m:
        return m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("No JSON found in model output")
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1]


def grounded_text(prompt: str, system: str | None = None) -> tuple[str, list[dict]]:
    """Text answer grounded with Google Search. Returns (text, citations)."""
    body = dict(model=cfg()["models"]["text"], input=prompt, tools=[{"type": "google_search"}])
    if system:
        body["system_instruction"] = system
    it = _call(**body)
    return _output_text(it), citations(it)


def structured(prompt: str, schema: Type[T], system: str | None = None, search: bool = False) -> T:
    """Structured JSON output validated against a pydantic model."""
    body = dict(
        model=cfg()["models"]["text"],
        input=prompt,
        response_format={"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()},
    )
    if system:
        body["system_instruction"] = system
    if search:
        body["tools"] = [{"type": "google_search"}]
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            it = _call(**body)
        except Exception as e:
            # Structured output + search is a preview combo; fall back to free text + JSON extraction.
            if search and "response_format" in body:
                log.warning("Structured+search rejected (%s); falling back to text JSON", e)
                body.pop("response_format")
                body["input"] = prompt + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema.model_json_schema())
                continue
            raise
        raw = _output_text(it)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as e:
            try:
                return schema.model_validate_json(_extract_json(raw))
            except Exception:
                last_err = e
                log.warning("Schema validation failed (attempt %d): %s", attempt + 1, str(e)[:300])
    raise RuntimeError(f"Gemini returned invalid JSON for {schema.__name__}: {last_err}")


def tts(text: str, voice: str, style: str) -> bytes:
    """Synthesize speech. Returns WAV bytes (24 kHz mono 16-bit PCM)."""
    it = _call(
        model=cfg()["models"]["tts"],
        input=[{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{"type": "speech_metadata", "style": style}],
            }],
        }],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    audio = _get(it, "output_audio")
    data = _get(audio, "data")
    if not data:
        raise RuntimeError("Gemini TTS returned no audio")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if raw[:4] != b"RIFF":  # raw PCM — wrap in a WAV header
        raw = _pcm_to_wav(raw)
    return raw


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def image(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Generate an illustration. Returns JPEG bytes (SynthID-watermarked by Google)."""
    it = _call(
        model=cfg()["models"]["image"],
        input=prompt,
        response_format={"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspect_ratio, "image_size": "2K"},
    )
    img = _get(it, "output_image")
    data = _get(img, "data")
    if not data:
        raise RuntimeError("Gemini image model returned no image")
    return base64.b64decode(data) if isinstance(data, str) else bytes(data)
