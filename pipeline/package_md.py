"""Human-readable production package (the full per-video deliverable list) as Markdown."""
from __future__ import annotations


def _t(sec: float) -> str:
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def _cell(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ").strip()


def render_md(pkg, timeline, assets_per_scene, factcheck: list, kind: str, upload: dict | None) -> str:
    long = kind == "long"
    title = pkg.final_title if long else pkg.title
    out = [f"# {title}", ""]
    if upload:
        out += [f"**YouTube:** {upload['url']} — {upload['privacy']}" + (f", goes public {upload['publish_at']}" if upload.get("publish_at") else ""), ""]
    if long:
        out += ["## Title options", *[f"{i}. {t}" for i, t in enumerate(pkg.title_options, 1)], "",
                f"**Chosen:** {pkg.final_title} — {pkg.title_reason}", "", "## Hook", pkg.hook, ""]

    out += ["## Voiceover script", ""]
    for s in pkg.scenes:
        out.append(f"**[{s.section.upper()}]** {s.narration}")
        if s.pronunciation:
            out.append(f"  _Pronunciation: {s.pronunciation}_")
        out.append("")

    out += ["## Storyboard", "", "| Timestamp | Voiceover | Visual direction | AI image/video prompt | On-screen text | Sound design |", "|---|---|---|---|---|---|"]
    for s, tl, assets in zip(pkg.scenes, timeline, assets_per_scene):
        vis = "; ".join(f"{v.kind}: {v.query}" + (f" ({v.note})" if v.note else "") for v in s.visuals)
        used = ", ".join(sorted({a['source'] for a in assets}))
        prompts = " / ".join(v.ai_prompt for v in s.visuals)
        out.append(f"| {_t(tl['start'])}–{_t(tl['start'] + tl['duration'])} | {_cell(s.narration)} | {_cell(vis)} → used: {used} | {_cell(prompts)} | {_cell(s.on_screen_text)} | {_cell(s.sound_design)} |")
    out.append("")

    if long:
        out += ["## Thumbnail", f"- Text: **{pkg.thumbnail.text}**", f"- Concept: {pkg.thumbnail.concept}", f"- Prompt: {pkg.thumbnail.ai_prompt}", ""]
    out += ["## Description", pkg.description, "", "## Tags", ", ".join(pkg.tags), "", "## Hashtags", " ".join(pkg.hashtags), ""]
    if long:
        out += ["## Pinned comment", pkg.pinned_comment, ""]
    out += ["## Sources", *[f"- [{s.title}]({s.url})" + (f" — {s.used_for}" if s.used_for else "") for s in pkg.sources], ""]
    out += ["## Key claims", "| Claim | Status | Source |", "|---|---|---|",
            *[f"| {_cell(c.claim)} | {c.status} | {c.source_url} |" for c in pkg.claims], ""]
    if long:
        out += ["## Shorts from this documentary", *[f"{i}. **{x.hook}** — {x.idea} → {x.payoff}" for i, x in enumerate(pkg.shorts_ideas, 1)], ""]
        out += ["## QC checklist", *[f"- [ ] {c}" for c in pkg.qc_checklist], ""]

    out += ["## Fact-check rounds"]
    for i, r in enumerate(factcheck, 1):
        out.append(f"- Round {i}: **{r['verdict']}**, {len(r['issues'])} issue(s)")
        for iss in r["issues"]:
            out.append(f"  - [{iss['severity']}] scene {iss['scene_id']}: {iss['claim']} — {iss['problem']}")
    out.append("")

    out += ["## Asset licence log", "| Scene | Source | Author | Licence | Link | AI |", "|---|---|---|---|---|---|"]
    for s, assets in zip(pkg.scenes, assets_per_scene):
        for a in assets:
            out.append(f"| {s.id} | {a['source']} | {_cell(a.get('author', ''))} | {_cell(a.get('license', ''))} | {a.get('url', '')} | {'yes' if a.get('ai') else ''} |")
    return "\n".join(out) + "\n"
