#!/usr/bin/env python3
"""Rendermill parallel-render benchmark: sequential vs worker pool, REAL timings.

This harness drives the SAME code the real ``rendermill render`` path uses —
it imports the in-tree ``rendermill`` package and calls the actual phase
entry points that ``core.make_video`` now runs through the worker pool:

    core.render_pngs(..., workers=N)    # Chrome headless --screenshot
    core.make_audio_batch(..., workers=N)  # macOS `say` -> wav -> m4a (ffmpeg)

Nothing here is synthetic: every number is a wall-clock ``subprocess``/Chrome
call on this machine. The only difference between the two runs is the worker
count handed to the pool (``1`` = the original sequential loop).

The MP4 encode and concat phases are NOT benchmarked here: they were left
sequential in core (x264 is already multithreaded per invocation, so a pool
buys ~1.8x on an 11% slice), and GPU encode is a documented next step, not
implemented code — see ``rendermill.parallel.NEXT_STEPS`` and DESIGN.md.

Usage:
    python scripts/benchmark_parallel.py [--slides N] [--workers W[,W...]]
                                         [--out PATH.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Prefer the in-tree package over any editable install that may point at a
# different checkout (the editable install targets the main worktree).
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from rendermill import core, parallel  # noqa: E402
from rendermill.cache import Cache  # noqa: E402

DIMS = (1920, 1080)  # horizontal

LOCAL_TTS = {"provider": "local", "speaking_rate": 1.0}

_NARRATION = (
    "Here is the first point. Rendermill turns a stack of HTML slides into a "
    "finished narrated video, one pass from storyboard to shipped file.",
    "The second idea is caching. Every expensive step is keyed by a content "
    "hash, so re-rendering after a typo fix costs nothing at all.",
    "The third point is the preview gate. Before any paid API is touched you "
    "see every slide and can approve or abort the whole run.",
    "Point four covers voices. ElevenLabs when you have a key, Deepgram as a "
    "cheaper option, and a free local voice when you have neither.",
    "Point five is captions. Word aligned timing is burned into the video "
    "frame by frame, and never silently dropped.",
    "The sixth point is lipsync. An optional fal dot ai presenter overlays in "
    "the corner, driven by the same narration audio.",
    "Point seven is gates. The hook is measured and rejected if it runs too "
    "long, before the render spends a dollar.",
    "Point eight is the music bed. A backing track ducks under the narration "
    "with a sidechain compressor so the voice always wins.",
    "Point nine is clips. The master render auto emits pre timestamped hook "
    "and call to action segments, ready to post.",
    "And the final point. Every render ships a report with timings, gates, "
    "and cost, so the loop is measurable from start to finish.",
)


def _slide_html(idx: int, total: int) -> str:
    return f"""<!doctype html>
<meta charset="utf-8">
<style>
 :root {{ --bg:#0f1216; --panel:#161b22; --line:#2c333d; --text:#ebebe8;
          --muted:#8c919a; --accent:#fac85a; --good:#7cb37c; }}
 * {{ box-sizing:border-box; }}
 html,body {{ margin:0; padding:0; background:var(--bg); color:var(--text);
   font-family:-apple-system,"SF Pro Display",system-ui,sans-serif; }}
 body.canvas {{ width:1920px; height:1080px; padding:64px 96px;
   display:flex; flex-direction:column; overflow:hidden; }}
 .eyebrow {{ font-size:18px; letter-spacing:.22em; text-transform:uppercase;
   color:var(--accent); font-weight:700; margin-bottom:14px; }}
 h1 {{ font-size:64px; line-height:1.08; margin:0 0 18px; font-weight:800; }}
 p {{ font-size:24px; line-height:1.5; margin:0 0 18px; color:var(--text); }}
 .panel {{ background:var(--panel); border:1px solid var(--line);
   border-radius:14px; padding:32px 40px; margin-top:24px; }}
 .row {{ display:flex; gap:24px; margin-top:24px; }}
 .stat {{ flex:1; background:var(--panel); border:1px solid var(--line);
   border-radius:12px; padding:24px 28px; }}
 .stat b {{ font-size:40px; color:var(--accent); }}
 .muted {{ color:var(--muted); }}
</style>
<body class="canvas">
  <div class="eyebrow">Rendermill · slide {idx + 1:02d} of {total:02d}</div>
  <h1>Parallel render pipeline, point {idx + 1}</h1>
  <p>This slide carries a real headline, a full sentence of body copy, and a
  panel of figures so Chromium does genuine layout and raster work on it.</p>
  <div class="panel">
    <p>Slide index {idx} · phase timings are measured against this exact DOM.</p>
  </div>
  <div class="row">
    <div class="stat"><b>{idx * 7 + 1}%</b><br><span class="muted">hook retention</span></div>
    <div class="stat"><b>{idx * 11 + 3}</b><br><span class="muted">cache hits</span></div>
    <div class="stat"><b>{idx * 5 + 2}s</b><br><span class="muted">narration</span></div>
  </div>
</body>
"""


def build_slides(n: int, demo_dir: Path) -> list[dict]:
    demo_dir.mkdir(parents=True, exist_ok=True)
    slides = []
    for i in range(n):
        html_path = demo_dir / f"slide_{i:02d}.html"
        html_path.write_text(_slide_html(i, n))
        slides.append({
            "type": "html",
            "html": str(html_path),
            "narration": _NARRATION[i % len(_NARRATION)],
        })
    return slides


def run_pipeline(slides: list[dict], workdir: Path, workers: int) -> dict:
    """Run PNG -> TTS in `workdir` with `workers` threads, timing each phase."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # PNG phase — parallelized Chrome screenshots.
    t0 = time.perf_counter()
    core.render_pngs(slides, workdir=workdir, project_dir=None,
                     dimensions=DIMS, workers=workers)
    t_png = time.perf_counter() - t0

    # TTS phase — parallelized narration synthesis (local `say`).
    cache = Cache(workdir)
    rates = [1.0] * len(slides)
    t0 = time.perf_counter()
    core.make_audio_batch(
        slides, workdir=workdir, cache=cache, tts_cfg=LOCAL_TTS,
        resolved_provider="local", rates=rates, workers=workers,
    )
    t_tts = time.perf_counter() - t0

    total = t_png + t_tts
    return {
        "png": round(t_png, 3),
        "tts": round(t_tts, 3),
        "total": round(total, 3),
        "tts_workers": parallel.tts_workers("local", workers),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", type=int, default=10)
    ap.add_argument("--workers", default="1,8",
                    help="comma list; 1 = sequential baseline")
    ap.add_argument("--out", default=None, help="write results JSON here")
    args = ap.parse_args()

    worker_list = [int(w) for w in args.workers.split(",")]
    base = REPO / "scripts" / ".bench-demo"
    slides = build_slides(args.slides, base)
    print(f"[bench] {len(slides)} slides · html in {base}")
    print(f"[bench] workers default = {parallel.workers_default()} "
          f"(cores={parallel.cpu_cores()}) · say speedup ceiling = "
          f"{parallel.SAY_SPEEDUP_CEILING:.0f}x")
    print(f"[bench] tts provider = local (macOS `say`) · dims = {DIMS}")
    print()

    results = {}
    for w in worker_list:
        label = "sequential" if w == 1 else f"parallel x{w}"
        wd = REPO / "scripts" / ".bench-work" / f"w{w:02d}"
        print(f"[bench] {label:14s} -> {wd}")
        ph = run_pipeline(slides, wd, w)
        results[label] = ph
        print(f"         png {ph['png']:>8.3f}s   tts {ph['tts']:>8.3f}s "
              f"(workers={ph['tts_workers']})   TOTAL {ph['total']:>8.3f}s")

    seq = results.get("sequential")
    print("\n[bench] speedup vs sequential (seq/par, higher = faster):")
    for label, ph in results.items():
        if label == "sequential" or not seq:
            continue
        parts = []
        for k in ("png", "tts", "total"):
            if seq[k] > 0:
                parts.append(f"{k} {seq[k] / ph[k]:.2f}x")
        print(f"         {label:14s} " + "   ".join(parts))

    print("\n[bench] why not 10x locally (DESIGN.md §2, all measured):")
    print("  - macOS `say` serializes at ~2x (shared speech lock) -> TTS ceiling")
    print("  - Chrome spawn contention caps PNG at ~2x (persistent CDP is ~5-8x)")
    print("  - x264 already multithreaded -> MP4 bounded by the encoder, not the pool")
    print("  - 10x = GPU encode + cloud render queue (documented, not coded):")
    print("      see rendermill.parallel.NEXT_STEPS")

    payload = {
        "machine": {
            "cpu_cores": parallel.cpu_cores(),
            "workers_default": parallel.workers_default(),
            "say_parallel_ceiling": parallel.SAY_SPEEDUP_CEILING,
            "tts": "local (macOS say)",
            "dims": list(DIMS),
            "slides": args.slides,
        },
        "results": results,
    }
    if args.out:
        op = Path(args.out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(payload, indent=2))
        print(f"\n[bench] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
