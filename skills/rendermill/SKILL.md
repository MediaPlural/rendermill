---
name: rendermill
description: Use when the user wants to produce a narrated slide video (explainer, demo, walkthrough, short, reel) with AI TTS, animated backdrops, burned-in captions, pre-cut clip segments, and machine-checked hook gates. Wraps the rendermill CLI at ~/rendermill. Triggers on "make a video about X", "turn this into a narrated walkthrough", "build a short for Shorts/TikTok/Reels", "render the explainer", "emit clip cuts", "rendermill".
---

# Rendermill (Hermes skill port, spec §5.8)

The render pipeline. You drive it from the terminal; the mill ships
the MP4 plus pre-timestamped clip segments, a render report, and
machine-checked quality gates.

## Locate the engine

```
[ -d ~/rendermill ] && \
  MILL=~/rendermill
[ -z "$MILL" ] && MILL=$(find ~ -maxdepth 5 -name pyproject.toml \
    -path '*rendermill*' 2>/dev/null | head -1 | xargs dirname)
```

The CLI is installed in the repo venv: `$MILL/.venv/bin/rendermill`
(falls back to `rendermill` on PATH). The full operator manual is the
bundled Claude skill at `$MILL/rendermill/skill_data/rendermill/SKILL.md`
— read it before your first render of the day.

## The fast loop (what 90% of requests reduce to)

1. Brief -> scaffold: `$MILL/.venv/bin/rendermill init <name>` (or reuse a
   project dir with `slides.py` + `slides/*.html`).
2. Author `slides.py`: `SLIDES = [...]` + `CONFIG = {...}`. Per-slide:
   `{"type": "html", "html": "slides/hook.html", "narration": "...",
     "backdrop": "<abs path>.mp4", "role": "hook"}`.
3. Gate-aware writing: slide-1 narration ~7 words (~3s spoken), opening
   frame never text-only (give it a `backdrop`). If `explainer: True`,
   declare roles so question lands by ~23s and stakes by ~33s.
4. Headless render (agent lane): 
   `$MILL/.venv/bin/rendermill render --yes --no-preview`
   Interactive: drop both flags for the preview gate.
5. Output: `out/<name>.mp4` + `rendermill-report.json` + `clips/` segments
   + `clips/clips.json` manifest. Read the report JSON before declaring
   success: gate lines, per-slide durations, clip list.

## Modes

- **Batch / spend ceiling (§5.7):** `rendermill batch dir1 dir2
  --spend-ceiling 0.50` (+ `--dry-run` for the cost report alone).
  Halt on `[batch][yellow-gate]` means stop and ask the human; do NOT
  retry or raise the ceiling yourself.
- **Free / $0:** `CONFIG["mode"] = "free"` — local TTS (say/SAPI/espeak-ng), no paid APIs.
  Default choice for drafts and gate rehearsals.
- **Engine payload (§5.1, stubbed):** load Stage 4/5 JSON with
  `python -c "from rendermill.payload import load_payload; ..."` — the
  contract is documented in `$MILL/rendermill/payload.py`.

## Laws

- **Never skip the hook gate.** Exit code 3 = rewrite the opening, don't
  tune the gate.
- **Spend ceiling (the spend law):** paid TTS only through batch mode
  or an explicit ceiling; the yellow-gate halt needs human approval.
- **No credentials in the repo**; keys live in `~/.config/rendermill/keys.env`.
- **Provenance:** MIT fork of rendermill; keep LICENSE + attribution.
- **Output lands in the project's out/ dir or the engine renders/ dir.**
  Never write into the repo itself.
- **No em-dashes in narration or slide text.**
- **Real assets only** — no placeholder screenshots.

## Verification receipts

After every render, verify before reporting done:
```
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 out/<name>.mp4
cat rendermill-report.json     # gates + durations + clips
ls clips/                      # pre-timestamped segments exist
```

## Reference

- Spec: `the design spec` §5.1-5.10
- Backdrop library: `~/.config/rendermill/backdrops`
- Full manual: `$MILL/rendermill/skill_data/rendermill/SKILL.md`
- Test suite: `cd $MILL && .venv/bin/python -m pytest tests/ -q`