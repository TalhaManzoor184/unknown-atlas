"""Placeholder packages for offline render tests (`--mock`). Never uploaded."""
from __future__ import annotations

from .schemas import Claim, LongPackage, Scene, ShortIdea, ShortPackage, Source, Thumbnail, Visual

_SENT = ("This is a placeholder line used to test timing, captions and transitions in the render pipeline "
         "without calling any external service.")


def _scene(i: int, section: str, words: int, ost: str = "") -> Scene:
    base = (_SENT + " ") * (words // 20 + 1)
    return Scene(
        id=i, section=section, narration=" ".join(base.split()[:words]), on_screen_text=ost,
        visuals=[Visual(kind="archival_image", query=f"mock shot {i}a", ai_prompt="mock"),
                 Visual(kind="stock_video", query=f"mock shot {i}b", ai_prompt="mock")],
        sound_design="low ambient drone",
    )


def mock_long(topic: dict) -> LongPackage:
    sections = ["hook", "intro", "context", "context", "evidence", "evidence", "twist", "conclusion"]
    scenes = [_scene(i + 1, s, 12 if s == "hook" else 30, "1959 · URAL MOUNTAINS" if i == 2 else "") for i, s in enumerate(sections)]
    return LongPackage(
        title_options=[topic["title"], "Option two", "Option three"], final_title=topic["title"], title_reason="mock",
        hook=scenes[0].narration, scenes=scenes, description="Mock description.", tags=["mock", "test"],
        hashtags=["#mystery", "#history", "#documentary"], pinned_comment="What do you think happened?",
        thumbnail=Thumbnail(text="WHAT HAPPENED HERE?", concept="mock", ai_prompt="mock"),
        sources=[Source(title="Example", url="https://example.org")],
        claims=[Claim(claim="mock", status="verified")],
        shorts_ideas=[ShortIdea(hook="h", idea="i", payoff="p")] * 3, qc_checklist=["Check captions"],
    )


def mock_short(topic: dict, angle: str) -> ShortPackage:
    scenes = [_scene(i + 1, s, 14) for i, s in enumerate(["hook", "context", "evidence", "twist", "conclusion"])]
    return ShortPackage(title=angle[:60], scenes=scenes, description="Mock short.", tags=["mock"],
                        hashtags=["#shorts", "#mystery", "#facts"], sources=[Source(title="Example", url="https://example.org")],
                        claims=[Claim(claim="mock", status="verified")])
