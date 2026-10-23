"""Topic queue (content/topics.yaml) + publish state (state/published.json)."""
from __future__ import annotations

import datetime as dt
import json
import logging

import yaml

from . import gemini
from .config import ROOT, STATE_FILE, TOPICS_FILE
from .schemas import TopicBatch

log = logging.getLogger(__name__)
RESEARCH_DIR = ROOT / "content" / "research"


def load_topics() -> list[dict]:
    with open(TOPICS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f).get("topics", [])


def save_topics(topics: list[dict]):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump({"topics": topics}, f, sort_keys=False, allow_unicode=True, width=110)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"long": {}, "shorts": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def record(kind: str, key: str, data: dict):
    state = load_state()
    state["long" if kind == "long" else "shorts"][key] = {**data, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    save_state(state)


def next_long(topic_id: str | None = None) -> dict:
    topics, state = load_topics(), load_state()
    if topic_id:
        return next(t for t in topics if t["id"] == topic_id)
    for t in topics:
        if t["id"] not in state["long"] and not t.get("skip"):
            return t
    log.info("Topic queue empty — generating new topics")
    return refill()[0]


def next_short() -> tuple[dict, int, str]:
    """Prefer Shorts whose documentary is already out (so they can link to it)."""
    topics, state = load_topics(), load_state()
    for linked in (True, False):
        for t in topics:
            if t.get("skip") or (t["id"] in state["long"]) != linked:
                continue
            for i, angle in enumerate(t.get("shorts", [])):
                if f"{t['id']}#{i}" not in state["shorts"]:
                    return t, i, angle
    t = refill()[0]
    return t, 0, t["shorts"][0]


def refill(n: int = 6) -> list[dict]:
    topics = load_topics()
    existing = "\n".join(f"- {t['title']}" for t in topics)
    prompt = f"""Suggest {n} NEW topics for "Unknown Atlas", a cinematic mini-documentary YouTube channel for
USA/UK/Canada viewers. Pillars: mysteries (unexplained events), science (strange science), history (hidden history,
lost civilizations), places-space (strange places, natural phenomena, space discoveries). Rotate pillars.

Each topic must: have strong curiosity and a clear central question; be well documented by reliable sources
(no pure conspiracy topics); be visual (real photos, maps, footage exist); have at least 2 distinct Short angles;
ideally have a recent development. Avoid these existing topics:
{existing}"""
    batch = gemini.structured(prompt, TopicBatch, search=True)
    have = {t["id"] for t in topics}
    new = []
    for t in batch.topics:
        if t.id in have:
            continue
        d = t.model_dump()
        d["source"] = "auto"
        new.append(d)
    topics += new
    save_topics(topics)
    log.info("Added %d auto-generated topics", len(new))
    return new


def cached_research(topic_id: str) -> dict | None:
    p = RESEARCH_DIR / f"{topic_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_research(topic_id: str, research: dict):
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / f"{topic_id}.json").write_text(json.dumps(research, indent=2, ensure_ascii=False) + "\n")
