# Unknown Atlas — automated documentary pipeline

*The World Is Stranger Than You Think.*

A GitHub Actions pipeline for a faceless mini-documentary channel aimed at viewers in the US, UK, and Canada. It runs on a schedule, makes each video end to end, and uploads it to YouTube.

```
topic queue ─► Gemini + Google Search research ─► script package (Gemini, structured JSON)
            ─► fact-check with Google Search (auto-revise, fail if still wrong)
            ─► Gemini TTS narration (per scene) ─► licensed visuals (Wikimedia Commons / Pexels / optional AI)
            ─► FFmpeg render (Ken Burns, grade, captions, on-screen text, music ducking, loudness -14 LUFS)
            ─► thumbnail ─► YouTube upload (scheduled) ─► state + package log committed back to the repo
```

| Schedule (UTC) | Workflow | Output |
|---|---|---|
| Tue & Fri 15:41 | `long-form.yml` | A 6–10 min documentary, scheduled to go public at 20:00 UTC |
| Sun, Mon, Wed, Sat 12:47 | `shorts.yml` | A 20–45 s Short, scheduled to go public at 17:00 UTC |

That works out to about 9 documentaries and 17 Shorts a month. The first 30 days come from the 10 topics in `content/topics.yaml`. When the queue runs out, Gemini proposes new topics.

## What every video gets (`output/<video>/package.md`, also archived in `content/packages/`)

- Title options and the chosen title
- The hook and the full voiceover script
- A storyboard table: timestamp, voiceover, visual direction, AI prompt, on-screen text, sound
- Thumbnail concept and prompt
- Description, chapters, tags, hashtags, and the pinned comment
- Sources and the key claims, each marked verified, disputed, theory, or legend
- 3 Shorts ideas and a QC checklist
- Every fact-check round
- An **asset licence log** listing the source, author, licence, and link for every shot

## Setup (one time)

### 1. Secrets
Add these under repo → **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Where to get it |
|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `PEXELS_API_KEY` | https://www.pexels.com/api/ (free) |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Google Cloud OAuth client (Desktop app) with **YouTube Data API v3** enabled. You can reuse the client from the Sunny project. |
| `YOUTUBE_REFRESH_TOKEN` | Run `python scripts/get_refresh_token.py client_secret.json` on your PC. When it asks, choose the **Unknown Atlas** channel. |

### 2. Optional variables
Add these under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Default | Purpose |
|---|---|---|
| `PUBLISH_MODE` | `scheduled` | `private` = you publish by hand, `scheduled` = goes public automatically after a review window of at least 3 h, `public` = goes live immediately |
| `VOICE_NAME` | `Charon` | Gemini voice. You could also try `Sadaltager`, `Gacrux`, or `Iapetus`. |
| `AI_IMAGES` | on | Set to `false` to use only real archival images and stock footage |
| `GEMINI_TEXT_MODEL` / `GEMINI_TTS_MODEL` / `GEMINI_IMAGE_MODEL` | see `config.yaml` | Change a model without touching the code |

### 3. Music (optional)
Put tracks from the YouTube Audio Library into `assets/music/`. See the README in that folder.

### 4. First run
Go to **Actions → Long-form documentary → Run workflow** and set `upload = false`. Download the artifact, then watch the video and read `package.md`. When you're happy with it, turn uploads on.

## Local commands

```bash
pip install -r requirements.txt        # plus ffmpeg
python -m pipeline.main long --mock    # offline render test, no keys needed
python -m pipeline.main long --no-upload
python -m pipeline.main short
python -m pipeline.main long --topic franklin-expedition
python -m pipeline.main topics         # top up the topic queue
```

## Channel-safety rules built into the pipeline

- **Facts:** Scripts may only use the grounded research brief. The only sources they can cite are URLs that came back from Google Search. A separate grounded fact-check runs, and if a high-severity issue still remains after revisions, **the video is not uploaded**.
- **Licensing:** Wikimedia images are used only under PD, CC0, CC BY, or CC BY-SA, and attribution is written into the description automatically. Pexels assets are free for commercial use and credited anyway as a courtesy. Fonts are OFL.
- **AI disclosure:** If any AI-generated reconstruction appears in a video, `containsSyntheticMedia` is set and the description says so. AI is never used for real, identifiable people or to fake "archival" photos. The description also notes that the narration voice is AI.
- **Reused/inauthentic content:** Every video gets an original script, its own structure, and new research. It is not a template with the names swapped. Watch some of the uploads yourself, especially in the first weeks. Monetization is judged on what the channel looks like as a whole.
- **Nothing in this repo guarantees YPP approval or income.**

## Free-tier notes
- Gemini TTS is called once per scene, which means about 15 calls per documentary. If you hit 429 rate limits, the pipeline backs off and retries. Free-tier quotas change, so check AI Studio.
- YouTube API: one upload uses one call from the upload quota (100 a day). This channel needs about 1 a day.
- Custom thumbnails require a phone-verified channel. If thumbnail upload fails, the upload still succeeds.
- YouTube's API can't pin comments. The workflow run summary shows the pinned comment ready to paste.
