---
name: rendermill
description: Use when the user wants to produce a narrated slide video (explainer, demo, walkthrough, short, reel) with AI TTS, animated backdrops, burned-in captions, pre-cut clip segments, and machine-checked hook gates. Wraps the `rendermill` CLI. Triggers on phrases like "make a video about X", "turn this into a narrated walkthrough", "build a short for Shorts/TikTok/Reels", "render the explainer", "emit clip cuts".
---

# Making a narrated video

This skill drives the `rendermill` package (the render pipeline; MIT
fork of rendermill, internal module path `rendermill/`). The user describes
what they want. You write the slides and narration. The mill ships the MP4,
plus pre-timestamped clip segments and a render report.

## When to use this skill

- "Make me a video about X"
- "Turn this outline into a narrated walkthrough"
- "Build a short for YouTube Shorts / TikTok / Reels"
- "Render the explainer / wave-report video"
- "Cut clip segments out of the master for testing"

If the user wants a screen recording, point them at QuickTime or OBS instead.
This skill is for slide-based, narrated, captioned videos.

## Setup check (once per machine)

```
rendermill --version           # confirm CLI is installed
rendermill keys list           # confirm at least ELEVENLABS_API_KEY is set
```

If the CLI is missing, install from this repo (editable, keeps the fork
current):

```
cd ~/rendermill
.venv/bin/pip install -e .
```

You also need a Chromium-class browser on disk (Google Chrome assumed on
macOS) and ffmpeg + ffprobe on PATH. The renderer shells out to whichever
binary it finds; no Playwright install is required.

Optional, only if you want captions burned into the MP4 vs the SRT sidecar:
```
.venv/bin/pip install imageio-ffmpeg
```
Stock Homebrew ffmpeg lacks libass, so the burn step falls back to shipping
an SRT sidecar by default. `imageio-ffmpeg` carries a libass-enabled binary.

## The rendermill upgrades (spec §5)

This fork adds, on top of upstream rendermill:

- **§5.3 animated backdrops.** Per-slide `backdrop: "<path>.mp4"` fuses the
  slide PNG over looping deterministic simulation b-roll (library at
  `~/.config/rendermill/backdrops`). Biggest
  single quality lift; default it on for explainers.
- **§5.4 clip-first cuts.** `clips: {"emit": true}` auto-emits
  pre-timestamped segments (hook, every slide boundary, CTA) plus a
  `clips/clips.json` manifest beside the master.
- **§5.2 hook gate.** Renders whose slide-1 narration measures >3s, or whose
  opening frame is text-only, are REJECTED before the rest renders.
  Preflight is word-count ($0); the measured check runs right after slide-0
  TTS so a violation costs one slide, not a render.
- **§5.10 explainer gates.** `explainer: True` machine-checks hook <=3s,
  question by ~23s (hard 25s), stakes by ~33s (hard 35s), recap <=20s on
  the measured master timeline. Declare slide `role`s to enable.
- **§5.6 music bed.** `music: {track, gain, ducking}` mixes a bed under the
  narration via ffmpeg amix with voice-activated sidechain ducking.
- **§5.7 batch mode.** `rendermill batch <dirs>` renders a queue headless:
  cost report first, auto-proceed under the spend ceiling, Yellow-gate
  halt above it (the spend law).
- **§5.5 voice lane.** Default TTS voice is the default voice
  (`voice_id 9E7SCjnWJcKqJn9TkRLX`, `eleven_v3` pinned).
- **§5.1 engine payload.** Programmatic input from Stage 4/5: see
  `rendermill/payload.py` for the documented contract + loader.

## API keys

The minimum is one TTS key. Lipsync is opt-in per slide.

| Key | Required when | Where |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | Default TTS (also drives word-level captions) | https://elevenlabs.io/app/settings/api-keys |
| `FAL_API_KEY` | Any slide has `lipsync: True` | https://fal.ai/dashboard/keys |
| `DEEPGRAM_API_KEY` | Only if `tts.provider="deepgram"` (cheaper, no captions) | https://console.deepgram.com |

Resolution order at runtime: env var, then `~/.config/rendermill/keys.env`
(mode 0600). Set once with:
```
rendermill keys set EL=sk_xxx FAL=xxx
rendermill keys test
```

`keys test` marks unset optional keys as `[opt]`, not failures. Only required
keys fail loudly.

## Workflow

### Step 1: Brief

Before writing anything, get these from the user:

- **Topic + audience.** What's the video about, who is it for.
- **Orientation.** `horizontal` (YouTube, LinkedIn, embeds) or `short`
  (Shorts, TikTok, Reels).
- **Length target.** A 60-second short has different rhythm than a 5-minute
  explainer. Aim for ~10 seconds of narration per slide.
- **Visual assets.** Screenshots, diagrams, app clips. **Ask for real
  assets.** Do not generate placeholder screenshots. If the user has none,
  say so and ask whether they can capture them, before drafting slides
  that depend on assets that don't exist.
- **Lipsync presenter?** Yes/no. If yes, they need an avatar PNG at
  `assets/avatar.png`. Flatten the background on solid black before saving;
  transparent PNGs cause checker-pattern artifacts in the circle crop. The
  package ships a default Becky avatar that you can keep.

### Step 2: Scaffold

```
rendermill init <project-name>
cd <project-name>
```

This drops a `slides.py` (with `SLIDES = [...]` and `CONFIG = {...}`), three
starter HTML slides under `slides/`, and the default `assets/avatar.png`.

### Step 2b: Pull real assets (optional, recommended)

Before authoring slides, populate `assets/` with the imagery the slides
will reference. The `fetch-asset` subcommand wraps the programmatic
sources documented in `references/assets.md`:

```
rendermill fetch-asset gh:owner/repo        # GitHub OG card -> assets/gh_repo.png
rendermill fetch-asset shot:https://...    # Chrome screenshot -> assets/shot_<slug>.png
rendermill fetch-asset readme:owner/repo   # First README image -> assets/readme_repo.png
rendermill fetch-asset yt:VIDEO_ID         # YouTube thumb -> assets/yt_id.jpg
rendermill fetch-asset --list              # show what's cached
```

**Caveat:** GitHub returns a generic fallback PNG for nonexistent repos
rather than 404ing. If `fetch-asset gh:typo/wrong` reports success, glance
at the saved file to confirm it's the real repo card.

### Step 3: Author slides.py

`slides.py` is the source of truth. Narration text, slide types, lipsync
flags, speed overrides, backdrops, roles all live here. Slide schema:

```python
{"type": "html", "html": "slides/intro.html",
 "narration": "...", "lipsync": True}
{"type": "image", "image": "assets/screenshot.png",
 "narration": "...", "title": "Optional title strip"}
{"type": "video", "video": "assets/clip.mp4",
 "narration": "...", "title": "..."}
```

Optional per-slide fields: `lipsync: True`, `speed: 1.2`, `title: "..."`,
`backdrop: "<mp4 path>"` (the §5.3 animated backdrop), and
`role: "hook" | "question" | "stakes" | "beat" | "payoff" | "recap" | "cta"`
(needed by the §5.10 explainer gates and the §5.4 clip labels).

CONFIG blocks this fork adds:

```python
"gates": {"hook_max_s": 3.0},   # §5.2; set None to disable the hook gate
"explainer": True,               # §5.10 timing gates on (needs roles)
"music": {"track": "assets/bed.mp3", "gain": 0.12, "ducking": 0.5},  # §5.6
"clips": {"emit": True},         # §5.4 auto-emit cut segments + manifest
"batch": {"spend_ceiling_usd": 1.00},  # §5.7
"tts": {"provider": "auto",
        "voice_id": "9E7SCjnWJcKqJn9TkRLX",   # §5.5 the default voice
        "model": "eleven_v3"},
```

### Step 4: Write slide HTMLs

In `slides/`, use the provided `_shared.css` for design tokens. Body class:
`canvas h` for horizontal, `canvas v` for short. Add a slide-specific
`<style>` block per file.

The narration carries the meaning. The slide is for **anchoring**: one
headline plus one supporting element (chart, stat row, quote, screenshot).
Do not write paragraphs on the slide that the narration will also speak.

See `references/style.md` for layout patterns and the design token palette.

### Step 5: Preview gate (mandatory before TTS spend, interactive runs)

```
rendermill preview
```

Renders every slide to PNG and opens a local grid in the browser. The user
inspects all slides visually before any paid API is called. Press `y` in the
terminal to proceed, anything else to abort.

Headless / agent-driven runs skip it explicitly with `rendermill render
--yes --no-preview`, or use batch mode (Step 7). The §5.2 hook gate still
runs in every mode; only the human eyeball gate is skipped.

### Step 6: Render

```
rendermill render
```

Pipeline: §5.2 word-count preflight ($0) -> slide PNGs -> preview gate ->
per-slide TTS (the measured §5.2 hook gate fires right after slide-0 audio;
a reject exits code 3 before the rest spends anything) -> slide MP4s
(§5.3 backdrop composite where `backdrop` is set) -> concat -> caption burn
-> §5.10 explainer gates (if `explainer: True`) -> §5.6 music bed (if
configured) -> §5.4 clip cuts (if `clips.emit`) -> `out/<name>.mp4` +
`rendermill-report.json` + `clips/` segments.

### Step 7: Batch mode (agent / headless lane)

```
rendermill batch proj1 proj2 --spend-ceiling 0.50
rendermill batch proj1 --dry-run     # cost report only, zero spend
```

Prints a per-project cost estimate, auto-proceeds while the running total
stays under the ceiling, and HALTS with a `[batch][yellow-gate]` message
(the spend law) when the next project would cross it. Free-mode and
macOS-say projects cost $0 and never trip the ceiling.

### Step 8: Iterate

The cache is content-hashed. Tweak a single narration string and re-render;
only that slide regenerates TTS. Same for slide HTML (only that PNG
re-renders), backdrops (hashed by PNG+audio+broll), and lipsync.

To bypass all caches: `MNV_FORCE_REGEN=1 rendermill render`.

## Hard rules

- **No em-dashes anywhere.** Not in narration, not in slide text, not in
  titles. Use comma, period, or colon. (Project owner's standing rule.)
- **Real assets only.** No placeholder screenshots, no lorem-ipsum diagrams.
  **Try programmatic sources first.** See `references/assets.md`.
- **Hook law (§5.2):** slide-1 narration fits ~3s spoken (about 7 words)
  and the opening frame carries a visual (backdrop, video, or imagery),
  never text alone. The gate machine-checks both; write to the gate.
- **Explainer law (§5.10):** with `explainer: True`, question by ~23s,
  stakes by ~33s, recap <=20s. Declare slide roles.
- **Visual variance ratio.** At least 1 image-bearing or backdrop slide
  per 4 slides.
- **No curator's voice in narration or slide text.** Flat, factual,
  observational.
- **Spend law (the spend law):** every paid-API lane runs under a spend
  ceiling with a Yellow-gate halt above it. Batch mode enforces it; never
  call TTS APIs outside the mill.
- **Credentials never in the repo.** Keys live in env or
  `~/.config/rendermill/keys.env`.
- **One thought per slide.** If you find yourself writing a second paragraph
  on a slide, split it into two slides.

## Costs to set expectations

Tell the user the budget up front:

- ElevenLabs v3: ~$0.60 per minute of audio; turbo v2.5 ~$0.30/min.
- fal.ai OmniHuman 1.5: $0.16 per second of generated lipsync video.
- Everything else (backdrops, captions, clips, music mix, local TTS): $0.
- Re-renders are free (content-hash cache).

### Free / $0 mode

Set `CONFIG["mode"] = "free"` for a render that costs nothing. It forces:

- **macOS `say` TTS** (offline; default voice `"Zoe (Premium)"`, falling back
  to `Samantha` if Zoe isn't downloaded). Override with `tts.macos_voice`.
- **No fal.ai lip-sync** — any slide `lipsync: True` is ignored.
- **Static presenter** — Becky (bundled `assets/avatar.png`) as a circular
  badge in the bottom-right of every slide. Override the image with
  `avatar.image`.
- **Free estimated-timing captions** — burned in locally, no API.

Use it when the user says "make a *free* video", "no budget", or "$0". macOS
only (Linux users keep ElevenLabs/Deepgram).

### Static avatar without free mode (paid TTS + presenter badge)

```python
"avatar": {
    "static": True,        # turn the overlay on
    "scope": "all",        # "all" | "intro" | "flagged"
    "image": None,         # path rel. to project; falls back to bundled Becky
    "diameter": 280,
},
```

The badge is composited at ffmpeg time, not animated; lips don't move.

## When things go wrong

- **`[hook-gate] REJECT` (exit 3).** Rewrite the opening line shorter and
  give slide 0 a visual. Not negotiable in ad-hook mode; tune with
  `gates.hook_max_s` only for non-ad formats.
- **`[gate][explainer] REJECT`.** Move the flagged role's slide earlier or
  cut narration before it.
- **`[batch][yellow-gate] HALT`.** A project would cross the spend ceiling.
  Get human approval or raise `--spend-ceiling`.
- **Caption burn fails.** Stock Homebrew ffmpeg lacks libass. `pip install
  imageio-ffmpeg` inside the same venv. Meanwhile an SRT sidecar ships at
  `video_build/captions.srt`.
- **Backdrop not found.** Use the absolute library path or one relative to
  the project dir.
- **Browser preview did not open.** The package prints the URL as a
  copy-paste banner before trying to launch the browser. Copy that URL.
- **Lipsync circle has checker pattern.** Avatar PNG has a transparent
  background. Flatten on solid black before saving.
- **Slide PNG looks wrong.** Check the HTML in a real browser at the canvas
  resolution (1920x1080 or 1080x1920). Headless Chrome will render exactly
  that size.

See `references/troubleshooting.md` for the full failure-mode catalog.

## Reference

- Spec: `marketing/engine/VIDEO-RENDER-BACKEND-SPEC-2026-10-02.md` §5
- Package source: this repo (MIT fork of
  https://github.com/MediaPlural/rendermill; attribution preserved)
- CLI help: `rendermill --help`, `rendermill <subcommand> --help`
- Design tokens and layout patterns: `references/style.md`
- Asset sourcing catalog: `references/assets.md`
- Common error messages and fixes: `references/troubleshooting.md`