"""Structured outputs requested from Gemini."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Visual(BaseModel):
    kind: Literal["stock_video", "archival_image", "map", "ai_image"] = Field(
        description="stock_video = Pexels footage (atmosphere, nature, generic action); "
        "archival_image = real historical photo/painting/document from Wikimedia Commons; "
        "map = real map from Wikimedia Commons; "
        "ai_image = illustrative reconstruction when no real image can exist (use sparingly)."
    )
    query: str = Field(description="2-5 word search query for the stock/archive library, concrete nouns only")
    ai_prompt: str = Field(description="Detailed image-generation prompt used if no real asset is found. Cinematic, 16:9 or 9:16, no text in image, no real identifiable people.")
    note: str = Field(default="", description="Visual direction for this shot")


class Scene(BaseModel):
    id: int
    section: Literal["hook", "intro", "context", "evidence", "twist", "conclusion"]
    narration: str = Field(description="Exact voiceover text for this scene. Plain spoken English, no stage directions, no brackets.")
    on_screen_text: str = Field(default="", description="Optional 1-5 word overlay (a date, place, number). Empty if none.")
    visuals: list[Visual] = Field(description="1-3 shots for this scene, in order")
    sound_design: str = Field(default="", description="Music mood / sound effect suggestion")
    pronunciation: str = Field(default="", description="Pronunciation guidance for unusual names, e.g. 'Dyatlov = DYAHT-lov'")


class Source(BaseModel):
    title: str
    url: str
    used_for: str = ""


class Claim(BaseModel):
    claim: str
    status: Literal["verified", "disputed", "theory", "legend"]
    source_url: str = ""


class ShortIdea(BaseModel):
    hook: str
    idea: str
    payoff: str


class Thumbnail(BaseModel):
    text: str = Field(description="2-4 word overlay, uppercase, accurate to the story")
    concept: str
    ai_prompt: str = Field(description="Background image prompt, 16:9, dramatic but accurate, no text in image")


class LongPackage(BaseModel):
    title_options: list[str] = Field(description="Exactly 3 title options, each under 70 characters")
    final_title: str = Field(description="Chosen title, under 70 characters, not clickbait-misleading")
    title_reason: str
    hook: str = Field(description="The 5-10 second opening hook (also the first scene's narration)")
    scenes: list[Scene]
    description: str = Field(description="YouTube description body (2-4 short paragraphs + chapter timestamps NOT included). No source list (added automatically). No hashtags.")
    tags: list[str] = Field(description="10-20 search tags")
    hashtags: list[str] = Field(description="3 hashtags without spaces, e.g. #mystery")
    pinned_comment: str = Field(description="A genuine discussion question for the comments")
    thumbnail: Thumbnail
    sources: list[Source] = Field(description="Only URLs from the provided research sources")
    claims: list[Claim] = Field(description="Key factual claims in the script with their status")
    shorts_ideas: list[ShortIdea] = Field(description="3 Shorts concepts derived from this documentary, each a different fact")
    qc_checklist: list[str]


class ShortPackage(BaseModel):
    title: str = Field(description="Under 60 characters, curiosity-driven, accurate")
    scenes: list[Scene] = Field(description="4-8 fast scenes; visuals change every 3-6 seconds")
    description: str
    tags: list[str]
    hashtags: list[str] = Field(description="3 hashtags; include #shorts")
    sources: list[Source]
    claims: list[Claim]


class FactIssue(BaseModel):
    scene_id: int
    claim: str
    problem: str
    severity: Literal["low", "medium", "high"]
    fix: str = Field(description="Corrected narration wording for this claim")


class FactCheck(BaseModel):
    verdict: Literal["pass", "revise"]
    issues: list[FactIssue]


class TopicIdea(BaseModel):
    id: str = Field(description="kebab-case slug")
    title: str
    pillar: Literal["mysteries", "science", "history", "places-space"]
    angle: str
    why_now: str
    shorts: list[str] = Field(description="2 Shorts angles")


class TopicBatch(BaseModel):
    topics: list[TopicIdea]
