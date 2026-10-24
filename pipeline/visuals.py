"""Finds licensed visuals for every shot and logs where each one came from.

Order of preference per shot:
  archival_image / map -> Wikimedia Commons (PD / CC0 / CC BY / CC BY-SA only) -> Pexels/Pixabay photo -> AI -> card
  stock_video          -> Pexels video -> Pixabay video -> Pexels/Pixabay photo -> Wikimedia -> AI -> card
  ai_image             -> Gemini image (if enabled & under cap) -> Wikimedia -> Pexels/Pixabay photo -> card
Sources whose API key is not set are skipped automatically.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter

from . import gemini
from .config import cfg, env

log = logging.getLogger(__name__)
UA = {"User-Agent": "UnknownAtlasBot/1.0 (https://github.com/TalhaManzoor184/unknown-atlas)"}
STOP = set("a an the of in on at to and or for with from by is are was were this that old new photo image picture map".split())


class AssetPicker:
    def __init__(self, workdir: Path, portrait: bool, mock: bool = False):
        self.dir = workdir / "assets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.portrait = portrait
        self.mock = mock
        self.used: set[str] = set()
        self.ai_count = 0
        self.credits: list[dict] = []
        self.pexels_key = "" if mock else env("PEXELS_API_KEY", required=False)
        self.pixabay_key = "" if mock else env("PIXABAY_API_KEY", required=False)
        vc = cfg()["visuals"]
        self.ai_enabled = vc["ai_images"] and not mock
        has_stock = bool(self.pexels_key or self.pixabay_key)
        # Without any stock library, Gemini illustrations replace stock footage, so allow many more of them.
        self.ai_cap = int(vc["max_ai_images_per_video"] if has_stock else vc["max_ai_images_without_stock"])
        self.licenses = [l.lower() for l in vc["allowed_wikimedia_licenses"]]
        self.ai_failures = 0

    # ---------- public ----------
    def pick(self, visual, scene_text: str) -> dict:
        if self.mock:
            return self._card(visual.query, scene_text)
        photos = [self._pexels_photo, self._pixabay_photo]
        if self.pexels_key or self.pixabay_key:
            stock = [self._pexels_video, self._pixabay_video, *photos, self._wikimedia, self._ai]
        else:
            stock = [self._ai, self._wikimedia]   # atmosphere shots: AI first; real archive as fallback
        order = {
            "stock_video": stock,
            "archival_image": [self._wikimedia, *photos, self._ai],
            "map": [self._wikimedia, self._ai, *photos],
            "ai_image": [self._ai, self._wikimedia, *photos],
        }[visual.kind]
        for fn in order:
            try:
                asset = fn(visual)
            except Exception as e:  # one bad source must never kill the run
                log.warning("%s failed for %r: %s", fn.__name__, visual.query, e)
                asset = None
            if asset:
                self.credits.append(asset)
                log.info("Shot %r -> %s %s", visual.query, asset["source"], asset["type"])
                return asset
        return self._card(visual.query, scene_text)

    @property
    def used_ai(self) -> bool:
        return self.ai_count > 0

    # ---------- sources ----------
    def _download(self, url: str, suffix: str) -> Path:
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + suffix
        path = self.dir / name
        if not path.exists():
            with requests.get(url, headers=UA, timeout=60, stream=True) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
        return path

    def _pexels_video(self, v):
        if not self.pexels_key:
            return None
        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": v.query, "per_page": 15, "orientation": "portrait" if self.portrait else "landscape"},
            headers={"Authorization": self.pexels_key, **UA}, timeout=30,
        )
        r.raise_for_status()
        for vid in r.json().get("videos", []):
            key = f"pexels-video-{vid['id']}"
            if key in self.used or vid.get("duration", 0) < 4:
                continue
            files = [f for f in vid.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
            target = 1080 if self.portrait else 1920
            files.sort(key=lambda f: (abs(min(f["width"], f["height"]) - min(target, 1080)), -f["width"]))
            if not files:
                continue
            self.used.add(key)
            path = self._download(files[0]["link"], ".mp4")
            return {"path": str(path), "type": "video", "source": "Pexels", "author": vid.get("user", {}).get("name", ""),
                    "license": "Pexels License (free, no attribution required)", "url": vid.get("url", ""), "ai": False}
        return None

    def _pexels_photo(self, v):
        if not self.pexels_key:
            return None
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": v.query, "per_page": 15, "orientation": "portrait" if self.portrait else "landscape"},
            headers={"Authorization": self.pexels_key, **UA}, timeout=30,
        )
        r.raise_for_status()
        for p in r.json().get("photos", []):
            key = f"pexels-photo-{p['id']}"
            if key in self.used:
                continue
            self.used.add(key)
            path = self._download(p["src"]["large2x"], ".jpg")
            return {"path": str(path), "type": "image", "source": "Pexels", "author": p.get("photographer", ""),
                    "license": "Pexels License (free, no attribution required)", "url": p.get("url", ""), "ai": False}
        return None

    # Pixabay Content License: free for commercial use, no attribution required. Files are downloaded (no hotlinking).
    def _pixabay(self, endpoint: str, v, extra: dict) -> list[dict]:
        if not self.pixabay_key:
            return []
        r = requests.get(
            f"https://pixabay.com/api/{endpoint}",
            params={"key": self.pixabay_key, "q": v.query[:100], "safesearch": "true", "per_page": 20, "order": "popular", **extra},
            headers=UA, timeout=30,
        )
        r.raise_for_status()
        return r.json().get("hits", [])

    def _pixabay_video(self, v):
        target = 1080 if self.portrait else 1920
        for hit in self._pixabay("videos/", v, {"video_type": "film"}):
            key = f"pixabay-video-{hit['id']}"
            if key in self.used or hit.get("duration", 0) < 4:
                continue
            files = [f for f in (hit.get("videos") or {}).values() if f.get("url") and f.get("width")]
            if self.portrait:
                files = [f for f in files if f["height"] >= f["width"]] or files
            if not files:
                continue
            files.sort(key=lambda f: (abs(max(f["width"], f["height"]) - max(target, 1920)), -f["width"]))
            self.used.add(key)
            path = self._download(files[0]["url"], ".mp4")
            return {"path": str(path), "type": "video", "source": "Pixabay", "author": hit.get("user", ""),
                    "license": "Pixabay Content License (free, no attribution required)", "url": hit.get("pageURL", ""), "ai": False}
        return None

    def _pixabay_photo(self, v):
        extra = {"image_type": "photo", "orientation": "vertical" if self.portrait else "horizontal", "min_width": 1280}
        for hit in self._pixabay("", v, extra):
            key = f"pixabay-photo-{hit['id']}"
            if key in self.used:
                continue
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            self.used.add(key)
            path = self._download(url, ".jpg")
            return {"path": str(path), "type": "image", "source": "Pixabay", "author": hit.get("user", ""),
                    "license": "Pixabay Content License (free, no attribution required)", "url": hit.get("pageURL", ""), "ai": False}
        return None

    def _wikimedia(self, v):
        q = v.query
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 20,
                "gsrsearch": f"{q} filetype:bitmap|drawing", "prop": "imageinfo",
                "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 2400,
            },
            headers=UA, timeout=30,
        )
        r.raise_for_status()
        pages = sorted((r.json().get("query", {}).get("pages", {}) or {}).values(), key=lambda p: p.get("index", 99))
        qwords = {w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in STOP and len(w) > 2}
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            title = p.get("title", "")
            key = f"wm-{title}"
            if key in self.used or info.get("width", 0) < 800:
                continue
            if info.get("mime", "") in ("application/pdf", "image/vnd.djvu"):
                continue
            lic = _meta(meta, "LicenseShortName")
            if not lic or not any(lic.lower().startswith(a) for a in self.licenses):
                continue
            if _meta(meta, "NonFree").lower() == "true" or _meta(meta, "Restrictions"):
                continue
            hay = (title + " " + _meta(meta, "ImageDescription") + " " + _meta(meta, "Categories")).lower()
            if qwords and not any(w in hay for w in qwords):
                continue  # cheap relevance guard against off-topic results
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            self.used.add(key)
            path = self._download(url, Path(url).suffix.split("?")[0] or ".jpg")
            return {"path": str(path), "type": "image", "source": "Wikimedia Commons",
                    "author": _meta(meta, "Artist") or "Unknown", "license": lic,
                    "license_url": _meta(meta, "LicenseUrl"), "url": info.get("descriptionurl", ""),
                    "title": title.replace("File:", ""), "ai": False}
        return None

    def _ai(self, v):
        if not self.ai_enabled or self.ai_count >= self.ai_cap or self.ai_failures >= 3:
            return None
        prompt = (
            f"{v.ai_prompt}\n\nStyle: cinematic documentary illustration, painterly realism, moody natural lighting, "
            "subtle film grain. No text, no letters, no logos, no watermarks, no real identifiable people."
        )
        try:
            data = gemini.image(prompt, aspect_ratio="9:16" if self.portrait else "16:9")
        except Exception as e:
            m = str(e).lower()
            # quota / not-on-free-tier won't recover within this run: stop trying straight away
            self.ai_failures += 3 if ("quota" in m or "too_many_requests" in m or "429" in m) else 1
            if self.ai_failures >= 3:
                log.error("Gemini image generation failed 3 times — disabling AI images for this video")
            raise
        path = self.dir / (hashlib.sha1(prompt.encode()).hexdigest()[:16] + "_ai.jpg")
        path.write_bytes(data)
        self.ai_count += 1
        return {"path": str(path), "type": "image", "source": "AI illustration (Google Gemini)", "author": "",
                "license": "Generated for this video", "url": "", "ai": True}

    def _card(self, query: str, text: str) -> dict:
        """Last-resort abstract background (dark atmospheric gradient)."""
        w, h = (1080, 1920) if self.portrait else (1920, 1080)
        seed = int(hashlib.md5(query.encode()).hexdigest()[:6], 16)
        hue = [(20, 28, 40), (30, 22, 18), (16, 30, 30), (28, 20, 36)][seed % 4]
        img = Image.new("RGB", (w, h), hue)
        d = ImageDraw.Draw(img)
        for i in range(0, max(w, h), 6):
            a = int(40 * (1 - i / max(w, h)))
            d.ellipse([w * 0.5 - i, h * 0.45 - i, w * 0.5 + i, h * 0.45 + i], outline=tuple(min(255, c + a) for c in hue))
        img = img.filter(ImageFilter.GaussianBlur(30))
        path = self.dir / f"card_{seed}.jpg"
        img.save(path, quality=92)
        return {"path": str(path), "type": "image", "source": "card", "author": "", "license": "", "url": "", "ai": False}


def _meta(meta: dict, key: str) -> str:
    val = (meta.get(key) or {}).get("value", "")
    return html.unescape(re.sub(r"<[^>]+>", "", str(val))).strip()


def attribution_lines(credits: list[dict]) -> list[str]:
    """Credits for the description. Wikimedia CC BY / BY-SA require attribution; Pexels credited as courtesy."""
    lines, seen = [], set()
    for c in credits:
        if c["source"] == "Wikimedia Commons":
            line = f"• \"{c.get('title', 'Image')}\" by {c['author']} — {c['license']} — {c['url']}"
        elif c["source"] in ("Pexels", "Pixabay"):
            line = f"• Footage/photo by {c['author'] or c['source'] + ' contributor'} on {c['source']} — {c['url']}"
        elif c.get("ai"):
            line = "• Some illustrative reconstructions were generated with AI (Google Gemini)."
        else:
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines
