"""Grounded research: builds a fact brief with real, resolvable source URLs."""
from __future__ import annotations

import logging
import re

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


BRIEF_SECTIONS = """Produce a research brief with these sections:
1. Core story timeline (dates, places, people) — verified facts only.
2. Key evidence and discoveries (who found what, when, how we know).
3. Competing explanations/theories, each labelled VERIFIED / DISPUTED / THEORY / LEGEND, with which
   experts or studies support each.
4. The latest developments (most recent research or findings, with year).
5. Surprising, little-known but verifiable details useful as hooks or YouTube Shorts.
6. Common myths or misinformation about this topic that the video should NOT repeat as fact.
7. Pronunciation of unusual names.
Be specific and concise."""


def research(topic: dict) -> dict:
    try:
        return _grounded(topic)
    except gemini.QuotaError as e:
        log.warning("Google Search grounding not available (%s) — researching from Wikipedia instead", str(e)[:160])
        return _wikipedia(topic)


def _grounded(topic: dict) -> dict:
    prompt = f"""Research this documentary topic for a 6-10 minute YouTube mini-documentary.

Topic: {topic['title']}
Angle: {topic.get('angle', '')}

{BRIEF_SECTIONS} Cite sources."""
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
    return {"brief": text, "sources": sources, "method": "google_search"}


# ---------------------------------------------------------------- Wikipedia fallback (free, no search quota)

WIKI_API = "https://en.wikipedia.org/w/api.php"


def _wiki_search(query: str, limit: int = 4) -> list[str]:
    r = requests.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit,
                                       "format": "json", "formatversion": 2}, headers=UA, timeout=30)
    r.raise_for_status()
    return [h["title"] for h in r.json().get("query", {}).get("search", [])]


def _wiki_article(title: str, max_chars: int = 14000) -> dict | None:
    r = requests.get(WIKI_API, params={"action": "query", "prop": "extracts|info", "explaintext": 1, "inprop": "url",
                                       "titles": title, "redirects": 1, "format": "json", "formatversion": 2},
                     headers=UA, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    p = pages[0]
    text = p.get("extract", "")
    # drop reference-ish tail sections
    text = re.split(r"\n==\s*(See also|References|Notes|Further reading|External links|Bibliography)\s*==", text)[0]
    if len(text) < 800:
        return None
    return {"title": f"{p['title']} — Wikipedia", "url": p.get("fullurl") or f"https://en.wikipedia.org/wiki/{p['title'].replace(' ', '_')}",
            "text": text[:max_chars]}


def _wikipedia(topic: dict) -> dict:
    queries = [topic.get("wiki") or "", topic["title"].split(":")[0], topic["id"].replace("-", " ")]
    titles: list[str] = []
    for q in filter(None, queries):
        for t in _wiki_search(q):
            if t not in titles:
                titles.append(t)
        if len(titles) >= 4:
            break
    articles = [a for a in (_wiki_article(t) for t in titles[:4]) if a][:3]
    if not articles:
        raise RuntimeError(f"No usable Wikipedia articles found for {topic['title']!r}; add a `wiki:` search term to the topic")
    corpus = "\n\n".join(f"### SOURCE: {a['title']} ({a['url']})\n{a['text']}" for a in articles)
    prompt = f"""Write a research brief for a 6-10 minute YouTube mini-documentary.

Topic: {topic['title']}
Angle: {topic.get('angle', '')}

Use ONLY the source articles below. If something in the angle is not supported by them, say "not covered by sources".
{BRIEF_SECTIONS} After each fact, name the source article in brackets.

{corpus}"""
    brief = gemini.text(prompt, system=RESEARCH_SYSTEM.replace("Use Google Search. ", ""))
    sources = [{"title": a["title"], "url": a["url"]} for a in articles]
    log.info("Wikipedia research brief: %d chars from %s", len(brief), ", ".join(a["title"] for a in articles))
    return {"brief": brief, "sources": sources, "method": "wikipedia", "corpus": corpus}
