"""Grounded research: builds a fact brief with real, resolvable source URLs."""
from __future__ import annotations

import logging

import requests

from . import gemini

log = logging.getLogger(__name__)

RESEARCH_SYSTEM = (
    "You are a meticulous documentary researcher. Use Google Search. Only report facts you found in "
    "reliable sources (museums, universities, peer-reviewed journals, government archives, major news outlets, "
    "encyclopedias). For every important statement say whether it is VERIFIED fact, DISPUTED, a THEORY, or a "
    "LEGEND. Never invent quotes, numbers, dates or discoveries. If sources disagree, say so."
)

UA = {"User-Agent": "UnknownAtlasBot/1.0 (documentary research; contact via GitHub TalhaManzoor184/unknown-atlas)"}


def _resolve(url: str) -> str:
    """Grounding citations are often redirect URLs; resolve to the publisher URL."""
    if "grounding-api-redirect" not in url and "vertexaisearch" not in url:
        return url
    try:
        r = requests.head(url, allow_redirects=True, timeout=15, headers=UA)
        if r.url and "vertexaisearch" not in r.url:
            return r.url
        r = requests.get(url, allow_redirects=True, timeout=15, headers=UA, stream=True)
        return r.url
    except requests.RequestException:
        return url


def research(topic: dict) -> dict:
    prompt = f"""Research this documentary topic for a 6-10 minute YouTube mini-documentary.

Topic: {topic['title']}
Angle: {topic.get('angle', '')}

Produce a research brief with these sections:
1. Core story timeline (dates, places, people) — verified facts only.
2. Key evidence and discoveries (who found what, when, how we know).
3. Competing explanations/theories, each labelled VERIFIED / DISPUTED / THEORY / LEGEND, with which
   experts or studies support each.
4. The latest developments (most recent research or findings, with year).
5. Surprising, little-known but verifiable details useful as hooks or YouTube Shorts.
6. Common myths or misinformation about this topic that the video should NOT repeat as fact.
7. Pronunciation of unusual names.
Be specific and concise. Cite sources."""
    text, cites = gemini.grounded_text(prompt, system=RESEARCH_SYSTEM)
    sources = []
    seen = set()
    for c in cites:
        url = _resolve(c["url"])
        if url in seen:
            continue
        seen.add(url)
        sources.append({"title": c.get("title") or url, "url": url})
    log.info("Research brief: %d chars, %d sources", len(text), len(sources))
    if len(sources) < 3:
        log.warning("Only %d grounded sources found — fact quality may be weak", len(sources))
    return {"brief": text, "sources": sources}
