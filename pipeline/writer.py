"""Script + full video package generation, fact-checking and revision."""
from __future__ import annotations

import json
import logging

from . import gemini
from .config import cfg
from .schemas import FactCheck, LongPackage, ShortPackage

log = logging.getLogger(__name__)

WRITER_SYSTEM = """You are the head writer of "Unknown Atlas", a faceless cinematic mini-documentary YouTube channel
for viewers in the USA, UK and Canada. Tagline: "The World Is Stranger Than You Think."

Voice: natural, conversational American English that international viewers understand easily. Calm, curious,
intelligent — like a great documentary narrator, never a hype YouTuber. Short sentences. Vivid, concrete images.

Hard rules:
- Use ONLY facts from the research brief provided. Never invent facts, numbers, quotes, studies, or sources.
- Clearly separate verified facts from theories, disputed claims, legends and speculation in the narration itself
  ("one theory suggests...", "according to the 2021 study...", "legend says...").
- Curiosity gaps and open loops are good; misleading viewers or overstating evidence is not.
- No filler, no repeating information to pad length, no "smash that like button" spam.
- Original writing. Do not imitate any other creator's scripts, catchphrases, or style.
- Narration text must be speakable: no brackets, emojis, stage directions, URLs or markdown.
- Write numbers and years the way they should be spoken when ambiguous (e.g. "nineteen fifty-nine" is fine as "1959").
- Visuals: prefer real archival images and maps (Wikimedia Commons) for real people, places and artifacts; stock video
  for atmosphere. Use ai_image only for reconstructions that cannot be photographed; never fake an "archival" photo
  and never depict a real, identifiable person with AI.
- Sources list may contain ONLY URLs that appear in the provided research sources.
"""


def _sources_block(research: dict) -> str:
    return "\n".join(f"- {s['title']} — {s['url']}" for s in research["sources"]) or "(none)"


def write_long(topic: dict, research: dict) -> LongPackage:
    lf = cfg()["long_form"]
    prompt = f"""Write a complete long-form mini-documentary package.

TOPIC: {topic['title']}
ANGLE: {topic.get('angle', '')}
PILLAR: {topic.get('pillar', '')}

STRUCTURE (adjust to the story; no filler):
- hook (0:00-0:10): one powerful curiosity-driven opening line or two. Scene 1, section "hook".
- intro (0:10-0:30): introduce the mystery or central question.
- context (to ~2:00): essential background.
- evidence (~2:00-5:00): evidence, discoveries, events, explanations.
- twist (~5:00-7:30): unexpected developments, competing theories, deeper implications.
- conclusion: satisfying answer (or honest "we still don't know"), then ONE natural call to action
  (e.g. invite viewers to share their theory, or watch a related documentary). No begging.

LENGTH: {lf['target_words_min']}-{lf['target_words_max']} words of narration total,
across {lf['scenes_min']}-{lf['scenes_max']} scenes. Each scene 40-110 words.

Every scene needs 1-3 visuals. Use concrete search queries ("1959 Ural mountains snow tent", "Beinecke Library manuscript").
Use on_screen_text for dates, places and key numbers.

Also produce: 3 title options + final title (under 70 chars), YouTube description, 10-20 tags, 3 hashtags,
pinned comment (a genuine question), thumbnail concept (2-4 word text, accurate), sources (only from the list below),
key claims with status, 3 Shorts ideas (each a DIFFERENT standalone fact), and a QC checklist.

RESEARCH BRIEF:
{research['brief']}

RESEARCH SOURCES (the only URLs you may cite):
{_sources_block(research)}
"""
    pkg = gemini.structured(prompt, LongPackage, system=WRITER_SYSTEM)
    return _clean(pkg, research)


def write_short(angle: str, topic: dict, research: dict, long_title: str | None) -> ShortPackage:
    sh = cfg()["shorts"]
    link_line = (
        f'End by teasing the full documentary "{long_title}" (e.g. "The full story is on our channel.").'
        if long_title
        else "End with a line that makes viewers want to follow for more strange stories."
    )
    prompt = f"""Write a vertical YouTube Short (20-45 seconds).

TOPIC: {topic['title']}
SHORT ANGLE: {angle}

Rules:
- Scene 1 is an immediate hook (first 2 seconds matter most).
- ONE fascinating, verifiable idea. Concise storytelling. Satisfying payoff.
- {sh['target_words_min']}-{sh['target_words_max']} words total across 4-8 scenes (visual change every 3-6 seconds).
- {link_line}
- It must complement, not duplicate, a long documentary: focus on this single angle only.
- Visual queries should work in vertical framing (close-ups, single subjects).
- Hashtags: 3, including #shorts.

RESEARCH BRIEF:
{research['brief']}

RESEARCH SOURCES (the only URLs you may cite):
{_sources_block(research)}
"""
    pkg = gemini.structured(prompt, ShortPackage, system=WRITER_SYSTEM)
    return _clean(pkg, research)


def _clean(pkg, research):
    allowed = {s["url"] for s in research["sources"]}
    pkg.sources = [s for s in pkg.sources if s.url in allowed]
    if not pkg.sources:  # fall back to the grounded sources themselves
        from .schemas import Source

        pkg.sources = [Source(title=s["title"], url=s["url"]) for s in research["sources"][:8]]
    for c in pkg.claims:
        if c.source_url and c.source_url not in allowed:
            c.source_url = ""
    for i, s in enumerate(pkg.scenes, 1):
        s.id = i
        s.narration = " ".join(s.narration.replace("[", "").replace("]", "").split())
    return pkg


FACTCHECK_SYSTEM = (
    "You are a strict fact-checker for a documentary channel. Use Google Search to verify each factual claim in "
    "the script. Flag: wrong facts, invented details, speculation presented as fact, misleading framing, outdated "
    "information. Do not flag stylistic choices. 'high' = factually wrong or invented; 'medium' = misleading or "
    "overstated; 'low' = minor imprecision. verdict='pass' only if there are no high or medium issues."
)


def fact_check(pkg, research: dict) -> FactCheck:
    script = "\n".join(f"[scene {s.id}] {s.narration}" for s in pkg.scenes)
    prompt = f"Fact-check this narration script.\n\nTITLE: {getattr(pkg, 'final_title', None) or pkg.title}\n\n{script}"
    if research.get("method") == "wikipedia" or not gemini.search_available():
        # No Google Search on this key: check every claim against the source articles themselves.
        prompt += (
            "\n\nGoogle Search is not available. Verify each claim ONLY against the SOURCE ARTICLES below. "
            "Any specific fact (date, number, name, quote, finding) that the articles do not support counts as "
            "'high' severity (unsupported) unless it is common knowledge.\n\nSOURCE ARTICLES:\n"
            + research.get("corpus", research["brief"])
        )
    return gemini.structured(prompt, FactCheck, system=FACTCHECK_SYSTEM, search=True)


def revise(pkg, issues, research: dict):
    instruction = (
        "Revise this video package to fix the fact-check issues below. Keep everything else the same "
        "(structure, scene count, tone, visuals) unless a fix requires a change. Keep word count in range.\n\n"
        "FACT-CHECK ISSUES:\n" + json.dumps([i.model_dump() for i in issues], indent=2)
    )
    return rewrite(pkg, instruction, research)


def rewrite(pkg, instruction: str, research: dict):
    schema = type(pkg)
    prompt = f"""{instruction}

CURRENT PACKAGE (JSON):
{pkg.model_dump_json()}

RESEARCH SOURCES (the only URLs you may cite):
{_sources_block(research)}
"""
    return _clean(gemini.structured(prompt, schema, system=WRITER_SYSTEM), research)


def checked(pkg, research: dict):
    """Fact-check loop. Returns (package, report). Raises if high-severity issues remain."""
    rounds = cfg()["quality"]["max_factcheck_rounds"]
    report = []
    for r in range(rounds + 1):
        fc = fact_check(pkg, research)
        report.append(fc.model_dump())
        serious = [i for i in fc.issues if i.severity in ("high", "medium")]
        log.info("Fact-check round %d: verdict=%s, %d serious / %d total issues", r + 1, fc.verdict, len(serious), len(fc.issues))
        if not serious:
            return pkg, report
        if r == rounds:
            break
        pkg = revise(pkg, serious, research)
    high = [i for i in fc.issues if i.severity == "high"]
    if high:
        raise RuntimeError("Fact-check failed with unresolved high-severity issues: " + "; ".join(i.claim for i in high))
    log.warning("Medium-severity issues remain after revisions; continuing (see factcheck report)")
    return pkg, report


def word_count(pkg) -> int:
    return sum(len(s.narration.split()) for s in pkg.scenes)


def fit_length(pkg, lo: int, hi: int, research: dict, research_text_only: bool = False):
    """One corrective pass if narration is clearly outside the target word range."""
    wc = word_count(pkg)
    if lo * 0.85 <= wc <= hi * 1.1:
        return pkg
    direction = "Expand" if wc < lo else "Tighten"
    log.info("Narration is %d words (target %d-%d); asking for one %s pass", wc, lo, hi, direction.lower())
    instruction = (
        f"{direction} the narration so the total is between {lo} and {hi} words. "
        + ("Add depth using ONLY facts from the research sources — more evidence, context, and the competing "
           "explanations — never filler or repetition." if wc < lo else
           "Cut repetition and weaker details; keep the hook, the strongest evidence and the payoff.")
        + " Keep the same structure, sources rules and JSON fields."
    )
    return rewrite(pkg, instruction, research)
