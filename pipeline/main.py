"""Orchestrator.

    python -m pipeline.main long            # research -> script -> fact-check -> voice -> visuals -> render -> upload
    python -m pipeline.main short
    python -m pipeline.main long --no-upload  # everything except YouTube
    python -m pipeline.main long --mock       # offline render test (no API keys needed)
    python -m pipeline.main topics            # top up the topic queue
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
import sys
from pathlib import Path

from . import research as research_mod
from . import topics as tq
from . import visuals, voice, writer
from .config import ROOT, OUTPUT_DIR, cfg
from .package_md import render_md
from .render import render
from .captions import write_srt
from .schemas import LongPackage, ShortPackage
from .thumbnail import make_thumbnail

log = logging.getLogger("atlas")
CHAPTERS = {"hook": "The Mystery", "intro": "The Mystery", "context": "Background", "evidence": "The Evidence",
            "twist": "Theories & Twists", "conclusion": "What We Know Now"}


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def chapters(scenes, timeline) -> list[str]:
    out, last = [], None
    for s, tl in zip(scenes, timeline):
        label = CHAPTERS[s.section]
        if label != last:
            out.append((tl["start"], label))
            last = label
    out[0] = (0.0, out[0][1])
    # YouTube: >= 3 chapters, each >= 10s
    merged = [out[0]]
    for t, l in out[1:]:
        if t - merged[-1][0] >= 10:
            merged.append((t, l))
    return [f"{_fmt(t)} {l}" for t, l in merged] if len(merged) >= 3 else []


def build_description(pkg, credits, kind: str, timeline, long_url: str | None = None, synthetic: bool = False) -> str:
    parts = []
    if long_url:
        parts.append(f"▶ Watch the full documentary: {long_url}\n")
    parts.append(pkg.description.strip())
    if kind == "long":
        ch = chapters(pkg.scenes, timeline)
        if ch:
            parts.append("Chapters\n" + "\n".join(ch))
    parts.append("Sources\n" + "\n".join(f"• {s.title} — {s.url}" for s in pkg.sources))
    cr = visuals.attribution_lines(credits)
    if cr:
        parts.append("Visual credits\n" + "\n".join(cr))
    note = "Narration uses an AI voice. Every script is researched and fact-checked; theories and legends are labelled as such."
    if synthetic:
        note += " Some scenes are AI-generated reconstructions, not real photographs."
    parts.append(note)
    handle = cfg()["channel"]["handle_url"]
    if handle:
        parts.append(f"Subscribe for more: {handle}")
    parts.append(" ".join(h if h.startswith("#") else f"#{h}" for h in pkg.hashtags[:3]))
    return "\n\n".join(parts)


def collect_assets(pkg, workdir: Path, portrait: bool, mock: bool, timeline=None):
    """Enough distinct shots per scene that no single image/clip stays on screen too long."""
    picker = visuals.AssetPicker(workdir, portrait=portrait, mock=mock)
    shot_len = float(cfg()["shorts" if portrait else "long_form"].get("shot_seconds", 3.5 if portrait else 6.0))
    per_scene = []
    for i, s in enumerate(pkg.scenes):
        vis = s.visuals[:3]
        if not vis:
            per_scene.append([picker._card(s.narration[:30], "")])
            continue
        dur = timeline[i]["duration"] if timeline else shot_len * len(vis)
        need = max(len(vis), min(8, math.ceil(dur / shot_len)))
        shots, seen = [], set()
        for n in range(need):
            a = picker.pick(vis[n % len(vis)], s.narration)
            if a["path"] in seen:   # library ran out of fresh results for this query
                continue
            seen.add(a["path"])
            shots.append(a)
        per_scene.append(shots or [picker._card(s.narration[:30], "")])
    return picker, per_scene


def _frame(video: str, out: Path) -> str:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5", "-i", video, "-frames:v", "1", str(out)], check=True)
    return str(out)


def thumbnail_background(pkg, per_scene, workdir: Path, mock: bool, ai_ok: bool = True) -> str:
    if cfg()["visuals"]["ai_images"] and not mock and ai_ok:
        try:
            from . import gemini

            p = workdir / "thumb_bg.jpg"
            p.write_bytes(gemini.image(pkg.thumbnail.ai_prompt + "\nNo text, no letters, no logos. Dramatic, high contrast, one clear focal subject on the right side.", "16:9"))
            return str(p)
        except Exception as e:
            log.warning("AI thumbnail background failed: %s", e)
    flat = [a for scene in per_scene for a in scene]
    for want in ("Wikimedia Commons", "Pexels", "Pixabay"):
        for a in flat:
            if a["source"] == want and a["type"] == "image":
                return a["path"]
    for a in flat:
        if a["type"] == "video":
            return _frame(a["path"], workdir / "thumb_frame.jpg")
    return flat[0]["path"]


def summary(md: str):
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


# ------------------------------------------------------------------ long-form

def run_long(args):
    topic = tq.next_long(args.topic)
    slug = topic["id"]
    workdir = OUTPUT_DIR / f"long-{slug}"
    workdir.mkdir(parents=True, exist_ok=True)
    log.info("LONG-FORM: %s", topic["title"])
    lf = cfg()["long_form"]

    if args.mock:
        from .mock import mock_long

        research, pkg, report = {"brief": "", "sources": []}, mock_long(topic), []
    else:
        research = tq.cached_research(slug) or research_mod.research(topic)
        tq.save_research(slug, research)
        draft = load_draft(f"long-{slug}", LongPackage)
        if draft:
            pkg, report = draft
        else:
            pkg = writer.write_long(topic, research)
            pkg = writer.fit_length(pkg, lf["target_words_min"], lf["target_words_max"], research)
            pkg, report = writer.checked(pkg, research)
            save_draft(f"long-{slug}", pkg, report)
    (workdir / "package.json").write_text(pkg.model_dump_json(indent=2))
    log.info("Script: %d words, %d scenes", writer.word_count(pkg), len(pkg.scenes))

    narration, timeline = voice.narrate(pkg.scenes, workdir, mock=args.mock)
    picker, per_scene = collect_assets(pkg, workdir, portrait=False, mock=args.mock, timeline=timeline)
    video, total = render(pkg.scenes, timeline, per_scene, narration, workdir, workdir / f"{slug}.mp4", portrait=False, slug=slug,
                          pillar=topic.get("pillar", ""))
    srt = write_srt(pkg.scenes, timeline, workdir, workdir / "captions.en.srt")
    thumb = make_thumbnail(thumbnail_background(pkg, per_scene, workdir, args.mock, picker.ai_failures < 3), pkg.thumbnail.text, workdir / "thumbnail.jpg")

    synthetic = cfg()["visuals"]["disclose_ai_images"] and picker.used_ai
    desc = build_description(pkg, picker.credits, "long", timeline, synthetic=synthetic)
    (workdir / "description.txt").write_text(desc)

    up = None
    if not (args.no_upload or args.mock):
        from . import youtube

        up = youtube.upload(video, pkg.final_title, desc, pkg.tags, "long", thumb, synthetic=synthetic, captions=srt)
        tq.record("long", slug, {"title": pkg.final_title, **up, "seconds": round(total)})
        drop_draft(f"long-{slug}")

    md = render_md(pkg, timeline, per_scene, report, "long", up)
    (workdir / "package.md").write_text(md)
    _archive(slug, "long", md, args)
    summary(f"## 🎬 {pkg.final_title}\n- Length: {_fmt(total)}\n- Upload: {up['url'] + ' (' + up['privacy'] + (', public at ' + up['publish_at'] if up['publish_at'] else '') + ')' if up else 'skipped'}\n"
            f"- AI images used: {picker.ai_count} (synthetic flag: {synthetic})\n\n**Pin this comment:**\n> {pkg.pinned_comment}\n")
    return workdir


# ------------------------------------------------------------------ shorts

def run_short(args):
    topic, idx, angle = tq.next_short()
    key = f"{topic['id']}#{idx}"
    slug = f"{topic['id']}-s{idx + 1}"
    workdir = OUTPUT_DIR / f"short-{slug}"
    workdir.mkdir(parents=True, exist_ok=True)
    long_entry = tq.load_state()["long"].get(topic["id"])
    log.info("SHORT: %s — %s (linked: %s)", topic["title"], angle, bool(long_entry))

    if args.mock:
        from .mock import mock_short

        research, pkg, report = {"brief": "", "sources": []}, mock_short(topic, angle), []
    else:
        research = tq.cached_research(topic["id"]) or research_mod.research(topic)
        tq.save_research(topic["id"], research)
        draft = load_draft(f"short-{slug}", ShortPackage)
        if draft:
            pkg, report = draft
        else:
            pkg = writer.write_short(angle, topic, research, long_entry["title"] if long_entry else None)
            sh = cfg()["shorts"]
            pkg = writer.fit_length(pkg, sh["target_words_min"], sh["target_words_max"], research)
            pkg, report = writer.checked(pkg, research)
            save_draft(f"short-{slug}", pkg, report)
    (workdir / "package.json").write_text(pkg.model_dump_json(indent=2))

    narration, timeline = voice.narrate(pkg.scenes, workdir, mock=args.mock)
    spoken = timeline[-1]["start"] + timeline[-1]["duration"]
    if spoken > cfg()["shorts"]["max_seconds"]:
        raise RuntimeError(f"Short narration is {spoken:.0f}s — over the {cfg()['shorts']['max_seconds']}s limit")
    picker, per_scene = collect_assets(pkg, workdir, portrait=True, mock=args.mock, timeline=timeline)
    video, total = render(pkg.scenes, timeline, per_scene, narration, workdir, workdir / f"{slug}.mp4", portrait=True, slug=slug,
                          pillar=topic.get("pillar", ""))

    srt = write_srt(pkg.scenes, timeline, workdir, workdir / "captions.en.srt")
    synthetic = cfg()["visuals"]["disclose_ai_images"] and picker.used_ai
    if not any(h.lower() == "#shorts" for h in pkg.hashtags):
        pkg.hashtags = ["#shorts", *pkg.hashtags][:3]
    desc = build_description(pkg, picker.credits, "short", timeline, long_url=long_entry["url"] if long_entry else None, synthetic=synthetic)
    (workdir / "description.txt").write_text(desc)

    up = None
    if not (args.no_upload or args.mock):
        from . import youtube

        up = youtube.upload(video, pkg.title, desc, pkg.tags, "short", None, synthetic=synthetic, captions=srt)
        tq.record("short", key, {"title": pkg.title, **up, "seconds": round(total), "parent": topic["id"]})
        drop_draft(f"short-{slug}")

    md = render_md(pkg, timeline, per_scene, report, "short", up)
    (workdir / "package.md").write_text(md)
    _archive(slug, "short", md, args)
    summary(f"## 📱 Short: {pkg.title}\n- Length: {total:.0f}s\n- Upload: {up['url'] if up else 'skipped'}\n- Linked documentary: {long_entry['url'] if long_entry else 'none yet'}\n")
    return workdir


DRAFTS = ROOT / "content" / "drafts"


def load_draft(name: str, schema):
    """A fact-checked script saved by an earlier run (so a retry skips the slow writing steps)."""
    p = DRAFTS / f"{name}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    log.info("Reusing fact-checked draft %s", p.name)
    return schema.model_validate(data["package"]), data["factcheck"]


def save_draft(name: str, pkg, report):
    DRAFTS.mkdir(parents=True, exist_ok=True)
    (DRAFTS / f"{name}.json").write_text(json.dumps({"package": pkg.model_dump(), "factcheck": report}, indent=2, ensure_ascii=False) + "\n")


def drop_draft(name: str):
    (DRAFTS / f"{name}.json").unlink(missing_ok=True)


def _archive(slug: str, kind: str, md: str, args):
    """Keep a copy of every package in the repo (scripts + sources + licence log) when it's a real run."""
    if args.mock:
        return
    d = ROOT / "content" / "packages"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{kind}-{slug}.md").write_text(md)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Unknown Atlas pipeline")
    ap.add_argument("mode", choices=["long", "short", "topics"])
    ap.add_argument("--topic", help="force a topic id (long-form only)")
    ap.add_argument("--no-upload", action="store_true", help="render but do not upload")
    ap.add_argument("--mock", action="store_true", help="offline test with placeholder script/voice/visuals")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)

    try:
        if not args.mock:
            preflight(upload=not args.no_upload)
        if args.mode == "topics":
            new = tq.refill()
            print(json.dumps([t["title"] for t in new], indent=2))
            return
        out = run_long(args) if args.mode == "long" else run_short(args)
        print(f"OUTPUT_DIR={out}")
    except Exception as e:
        log.exception("Pipeline failed")
        if os.getenv("GITHUB_ACTIONS"):
            # surfaces in the run's annotations (readable without downloading logs)
            msg = f"{type(e).__name__}: {e}".replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:3000]
            print(f"::error title=Unknown Atlas pipeline failed::{msg}")
        sys.exit(1)


def preflight(upload: bool):
    needed = ["GEMINI_API_KEY"]
    if upload:
        needed += ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]
    missing = [n for n in needed if not os.getenv(n, "").strip()]
    if missing:
        raise RuntimeError("Missing GitHub secrets: " + ", ".join(missing) + " (repo Settings → Secrets and variables → Actions)")
    if not (os.getenv("PEXELS_API_KEY", "").strip() or os.getenv("PIXABAY_API_KEY", "").strip()):
        log.warning("No PEXELS_API_KEY / PIXABAY_API_KEY — using Gemini illustrations + Wikimedia images instead of stock footage")
        if os.getenv("GITHUB_ACTIONS"):
            print("::warning title=No stock footage key::Set PIXABAY_API_KEY or PEXELS_API_KEY for stock footage")


if __name__ == "__main__":
    main()
