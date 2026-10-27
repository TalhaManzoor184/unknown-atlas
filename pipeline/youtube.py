"""YouTube Data API v3 upload (OAuth refresh token stored in GitHub secrets)."""
from __future__ import annotations

import datetime as dt
import logging
import time

from .config import cfg, env

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
        client_id=env("YOUTUBE_CLIENT_ID"),
        client_secret=env("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        # no explicit scopes: use whatever the refresh token was granted (youtube + force-ssl for captions)
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _taken_slots() -> set[str]:
    """publishAt times already used by scheduled uploads (so two videos never go public at the same minute)."""
    from .topics import load_state

    st = load_state()
    return {v.get("publish_at", "")[:16] for group in ("long", "shorts") for v in st.get(group, {}).values()}


def publish_time(kind: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Earliest free configured slot that leaves at least `min_review_hours` for review."""
    p = cfg()["publishing"]
    if p["mode"] != "scheduled":
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = p["long_publish_utc"] if kind == "long" else p.get("short_publish_slots_utc") or [p["short_publish_utc"]]
    slots = sorted(map(lambda x: tuple(map(int, x.split(":"))), raw if isinstance(raw, list) else [raw]))
    earliest = now + dt.timedelta(hours=float(p["min_review_hours"]))
    taken = _taken_slots()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(30):
        for hh, mm in slots:
            slot = day.replace(hour=hh, minute=mm)
            if slot >= earliest and slot.strftime("%Y-%m-%dT%H:%M") not in taken:
                return slot
        day += dt.timedelta(days=1)
    return earliest


def upload(video_path, title: str, description: str, tags: list[str], kind: str,
           thumbnail_path=None, synthetic: bool = False, captions=None) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = _service()
    p = cfg()["publishing"]
    status = {"selfDeclaredMadeForKids": False, "embeddable": True, "containsSyntheticMedia": bool(synthetic)}
    when = publish_time(kind)
    if p["mode"] == "public":
        status["privacyStatus"] = "public"
    else:
        status["privacyStatus"] = "private"
        if when:
            status["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": _trim_tags(tags),
            "categoryId": cfg()["channel"]["category_id"],
            "defaultLanguage": cfg()["channel"]["language"],
            "defaultAudioLanguage": cfg()["channel"]["language"],
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, retries = None, 0
    while resp is None:
        try:
            st, resp = req.next_chunk()
            if st:
                log.info("Upload %d%%", int(st.progress() * 100))
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                time.sleep(2 ** retries)
                continue
            raise
    vid = resp["id"]
    log.info("Uploaded video %s (%s%s)", vid, status["privacyStatus"], f", publishAt {status.get('publishAt')}" if when else "")

    if thumbnail_path:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")).execute()
        except HttpError as e:  # needs a verified channel; don't fail the run
            log.warning("Thumbnail upload failed (is the channel phone-verified?): %s", e)

    if captions:
        try:  # 400 quota units; gives viewers a proper CC track (and helps search)
            yt.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": vid, "language": "en", "name": "English", "isDraft": False}},
                media_body=MediaFileUpload(str(captions), mimetype="application/octet-stream"),
            ).execute()
            log.info("Uploaded English captions")
        except HttpError as e:
            log.warning("Caption upload failed: %s", e)

    pl = cfg()["channel"]["playlist_long" if kind == "long" else "playlist_shorts"]
    if pl:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        except HttpError as e:
            log.warning("Playlist insert failed: %s", e)

    return {"video_id": vid, "url": f"https://youtu.be/{vid}", "privacy": status["privacyStatus"],
            "publish_at": status.get("publishAt", "")}


def _trim_tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = t.strip().replace("<", "").replace(">", "")
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if total + cost > 480:
            break
        out.append(t)
        total += cost
    return out
