"""Facebook Page publishing: one Reel, one image post and one Story per daily Short.

Graph API flows used (Page access token with pages_manage_posts, pages_read_engagement, pages_show_list):
  Reel   POST /{page}/video_reels upload_phase=start -> POST rupload.facebook.com/video-upload/{ver}/{video_id}
         (binary, "Authorization: OAuth <token>") -> POST /{page}/video_reels upload_phase=finish
  Image  POST /{page}/photos (multipart source + message)
  Story  POST /{page}/photos published=false -> POST /{page}/photo_stories photo_id=...
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from pathlib import Path

import requests
from pydantic import BaseModel, Field

from .config import cfg

log = logging.getLogger(__name__)


def _ver() -> str:
    return cfg().get("facebook", {}).get("graph_version", "v25.0")


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{_ver()}/{path.lstrip('/')}"


def enabled() -> bool:
    return bool(os.getenv("FB_PAGE_ID", "").strip() and os.getenv("FB_PAGE_TOKEN", "").strip())


def _creds() -> tuple[str, str]:
    return os.environ["FB_PAGE_ID"].strip(), os.environ["FB_PAGE_TOKEN"].strip()


def _check(r: requests.Response, what: str) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:500]}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        err = data.get("error", data) if isinstance(data, dict) else data
        raise RuntimeError(f"Facebook {what} failed ({r.status_code}): {err}")
    return data


# ------------------------------------------------------------------ copy

class SocialCopy(BaseModel):
    image_headline: str = Field(description="2-6 word hook for the image card, uppercase-friendly, accurate")
    image_fact: str = Field(description="One surprising, verifiable fact from the script, max 28 words, plain sentence")
    image_caption: str = Field(description="Facebook post text: 1-2 short sentences expanding the fact, then a genuine question to spark comments. No links. Max 60 words.")
    reel_caption: str = Field(description="Facebook Reel caption: one curiosity line + 'Follow Unknown Atlas for a new strange story every day.' Max 40 words. No links.")
    story_line: str = Field(description="2-5 word teaser for a Story card, e.g. 'NEW MYSTERY TODAY'")
    hashtags: list[str] = Field(description="3-5 Facebook hashtags like #mystery #history #facts")


def social_copy(pkg) -> SocialCopy:
    """Facebook texts written ONLY from the already fact-checked Short script."""
    from . import gemini

    script = " ".join(s.narration for s in pkg.scenes)
    try:
        return gemini.structured(
            "Write Facebook copy for this fact-checked YouTube Short. Use ONLY facts stated in the script below; "
            "do not add any new facts, numbers or claims. Natural American English, no clickbait that the script "
            f"does not support.\n\nTITLE: {pkg.title}\nSCRIPT: {script}",
            SocialCopy,
        )
    except Exception as e:  # never block posting on copywriting
        log.warning("Social copy generation failed (%s) — using script text", e)
        first = pkg.scenes[0].narration if pkg.scenes else pkg.title
        return SocialCopy(
            image_headline=pkg.title[:40], image_fact=" ".join(first.split()[:28]),
            image_caption=f"{first} What do you think really happened?",
            reel_caption=f"{pkg.title}. Follow Unknown Atlas for a new strange story every day.",
            story_line="NEW STORY TODAY", hashtags=["#mystery", "#history", "#facts"],
        )


def _tags(copy: SocialCopy) -> str:
    tags = [h if h.startswith("#") else f"#{h}" for h in copy.hashtags if h.strip()]
    return " ".join(dict.fromkeys(t.replace(" ", "") for t in tags[:5]))


# ------------------------------------------------------------------ publishing

def publish_reel(video: Path, caption: str, title: str, when: dt.datetime | None) -> dict:
    page, token = _creds()
    start = _check(requests.post(_graph(f"{page}/video_reels"), data={"upload_phase": "start", "access_token": token}, timeout=60),
                   "reel start")
    vid = start["video_id"]
    size = video.stat().st_size
    with open(video, "rb") as f:
        up = requests.post(
            f"https://rupload.facebook.com/video-upload/{_ver()}/{vid}",
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=f, timeout=600,
        )
    _check(up, "reel upload")

    finish = {"upload_phase": "finish", "video_id": vid, "description": caption, "title": title[:100], "access_token": token}
    # Facebook schedules between 10 minutes and 75 days ahead
    now = dt.datetime.now(dt.timezone.utc)
    if when and now + dt.timedelta(minutes=12) < when < now + dt.timedelta(days=74):
        finish.update(video_state="SCHEDULED", scheduled_publish_time=str(int(when.timestamp())))
    else:
        finish["video_state"] = "PUBLISHED"
    try:
        _check(requests.post(_graph(f"{page}/video_reels"), data=finish, timeout=60), "reel finish")
    except RuntimeError as e:
        if finish["video_state"] != "SCHEDULED":
            raise
        log.warning("Scheduling the Reel failed (%s) — publishing it now instead", e)
        finish.pop("scheduled_publish_time")
        finish["video_state"] = "PUBLISHED"
        _check(requests.post(_graph(f"{page}/video_reels"), data=finish, timeout=60), "reel finish")

    # wait (briefly) for processing so failures show up in the log
    status = {}
    for _ in range(20):
        status = _check(requests.get(_graph(vid), params={"fields": "status", "access_token": token}, timeout=30), "reel status")
        vs = (status.get("status") or {}).get("video_status")
        if vs in ("ready", "error"):
            break
        time.sleep(15)
    st = status.get("status") or {}
    if st.get("video_status") == "error":
        raise RuntimeError(f"Facebook rejected the Reel while processing: {st}")
    log.info("Facebook Reel %s (%s)", vid, finish["video_state"].lower())
    return {"reel_id": vid, "reel_state": finish["video_state"].lower(),
            "reel_publish_at": when.isoformat() if finish["video_state"] == "SCHEDULED" else ""}


def publish_photo(image: Path, message: str) -> dict:
    page, token = _creds()
    with open(image, "rb") as f:
        data = _check(requests.post(_graph(f"{page}/photos"), data={"message": message, "access_token": token},
                                    files={"source": (image.name, f, "image/jpeg")}, timeout=120), "photo post")
    log.info("Facebook image post %s", data.get("post_id") or data.get("id"))
    return {"photo_post_id": data.get("post_id") or data.get("id")}


def publish_story(image: Path) -> dict:
    page, token = _creds()
    with open(image, "rb") as f:
        photo = _check(requests.post(_graph(f"{page}/photos"), data={"published": "false", "access_token": token},
                                     files={"source": (image.name, f, "image/jpeg")}, timeout=120), "story photo upload")
    data = _check(requests.post(_graph(f"{page}/photo_stories"), data={"photo_id": photo["id"], "access_token": token}, timeout=60),
                  "story publish")
    log.info("Facebook Story %s", data.get("post_id") or data)
    return {"story_id": data.get("post_id") or photo["id"]}


def post_daily(pkg, video: Path, per_scene, workdir: Path, yt: dict | None) -> dict:
    """Reel, "did you know" image post and Story, published together. Each part fails independently."""
    from .cards import feed_card, story_card

    copy = social_copy(pkg)
    tags = _tags(copy)
    # Facebook gets today's post straight away, so the Reel, image and Story all go out together
    # (the YouTube copy may sit in a later publish slot while a backlog is being worked off).
    when = None
    if cfg().get("facebook", {}).get("reel_follows_youtube_time") and yt and yt.get("publish_at"):
        when = dt.datetime.fromisoformat(yt["publish_at"].replace("Z", "+00:00"))

    result, errors = {}, []
    feed = feed_card(per_scene, video, copy.image_headline, copy.image_fact, workdir / "fb_feed.jpg")
    story = story_card(per_scene, video, copy.story_line, pkg.title, workdir / "fb_story.jpg")
    parts = [
        ("reel", lambda: publish_reel(video, f"{copy.reel_caption}\n\n{tags}", pkg.title, when)),
        ("image", lambda: publish_photo(feed, f"{copy.image_caption}\n\n{tags}")),
        ("story", lambda: publish_story(story)),
    ]
    for name, fn in parts:
        try:
            result.update(fn())
        except Exception as e:
            log.error("Facebook %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    if errors and os.getenv("GITHUB_ACTIONS"):
        msg = " | ".join(errors).replace("%", "%25").replace("\n", " ")[:2000]
        print(f"::warning title=Facebook posting problem::{msg}")
    result["facebook_errors"] = errors
    return result


def check() -> None:
    """Setup check: token valid, page reachable, permissions present."""
    page, token = _creds()
    info = _check(requests.get(_graph(page), params={"fields": "id,name,link", "access_token": token}, timeout=30), "page lookup")
    print(f"Page OK: {info.get('name')} ({info.get('id')}) {info.get('link', '')}")
    try:
        perms = _check(requests.get(_graph("me/permissions"), params={"access_token": token}, timeout=30), "permissions")
        granted = sorted(p["permission"] for p in perms.get("data", []) if p.get("status") == "granted")
        if granted:
            print("Granted:", ", ".join(granted))
    except RuntimeError:
        pass  # page tokens don't always expose /me/permissions
    need = {"pages_manage_posts", "pages_read_engagement", "pages_show_list"}
    print("Needs:", ", ".join(sorted(need)))
