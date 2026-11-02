"""Phase-parallel worker pool for the render loop.

Threads, not processes: every expensive primitive in the pipeline — Chrome
headless screenshots, macOS ``say``/provider TTS, ffmpeg encodes — is a
``subprocess`` call that releases the GIL while it runs, so a
``ThreadPoolExecutor`` saturates the same cores a ``ProcessPoolExecutor``
would, without the per-worker interpreter and pickle cost. A process pool is
only worth it once Python-side work (caption build, image compositing) grows;
see the rollout note at the bottom.

Measured ceilings (speed prototype, `DESIGN.md` §2 — wall-clock on 28-core
Apple Silicon, Chrome + ffmpeg 9.0.2 + macOS `say`):

- **macOS `say` serializes at ~2x.** 8 concurrent `say -o` calls finish in
  ~4.9 s vs ~10.2 s sequential (2.08x). The OS speech engine holds a shared
  lock, so extra workers buy nothing past ~2. This is a property of the
  *free local* voice, not a code defect; the paid providers (ElevenLabs,
  Deepgram) are network-bound and scale with concurrent requests instead.
- **Chrome has no profile lock** — parallel ``--screenshot`` forks a fresh
  Chrome per slide and contends on process spawn, capping the PNG phase at
  ~2x. The ~5-8x path is one persistent CDP-driven Chrome (documented next
  step, not implemented here).
- **x264 is already multithreaded** per invocation, so the encode phase is
  bounded by the encoder, not the pool.

The 10x target is therefore NOT a threading problem. It is three documented,
separately-shippable next steps (see ``NEXT_STEPS`` below), of which this
module is only step 1.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# macOS `say` shares one speech-engine lock. Measured 2.08x *speedup* ceiling:
# 8 concurrent `say -o` processes finish in ~4.9 s vs ~10.2 s sequential, and
# no worker count pushes past ~2x. This is a ceiling on *throughput*, NOT a
# reason to cap the worker count — fewer workers (e.g. 2) reach only ~1.8x
# because each `say` process still pays its own startup. So the local voice
# gets the full requested worker count and lets the OS lock do the throttling.
SAY_SPEEDUP_CEILING = 2.0

# Default cap for subprocess-bound phases. Beyond this, Chrome spawn and the
# `say` lock dominate and more threads mostly add contention (measured best
# was x8 on a 28-core machine; x16 was slightly *slower*).
DEFAULT_MAX_WORKERS = 8


def cpu_cores() -> int:
    """CPU count (never 0, so arithmetic below is always safe)."""
    return os.cpu_count() or 4


def workers_default() -> int:
    """Default worker count: ``min(cores, DEFAULT_MAX_WORKERS)``, >= 1."""
    return max(1, min(cpu_cores(), DEFAULT_MAX_WORKERS))


def effective_workers(requested: Optional[int]) -> int:
    """Clamp a requested count into ``[1, cores]``; ``None``/``0`` -> default."""
    if requested is None or requested <= 0:
        return workers_default()
    return max(1, min(requested, cpu_cores()))


def tts_workers(provider: Optional[str], requested: Optional[int]) -> int:
    """Worker count for the TTS phase.

    The local OS voice (``say``/SAPI/espeak-ng) gets the full requested count:
    its shared speech-engine lock caps the *speedup* at ~``SAY_SPEEDUP_CEILING``
    (2x) no matter how many workers run, but fewer workers reach only ~1.8x
    (per-process startup), so capping them would leave throughput on the table.
    Paid providers (ElevenLabs/Deepgram) are network-bound and scale with
    concurrent requests. ``provider`` is reserved for future per-provider
    policy (e.g. streaming TTS); today the worker count is provider-agnostic.
    """
    return effective_workers(requested)


def map_threads(fn: Callable[[T], R], tasks: Iterable[T],
                workers: int = 1) -> List[R]:
    """Run ``fn`` over ``tasks`` on a thread pool, preserving input order.

    ``workers <= 1`` (or an empty task list) runs inline on the caller's
    thread — no executor is spun up for the sequential case, so the baseline
    path is byte-for-byte the old loop.
    """
    tasks = list(tasks)
    if not tasks:
        return []
    if workers <= 1:
        return [fn(t) for t in tasks]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, tasks))


# ---------------------------------------------------------------------------
# Rollout order past this module (each independently shippable; all measurable
# with scripts/benchmark_parallel.py). Not implemented here on purpose.
# ---------------------------------------------------------------------------
NEXT_STEPS = (
    # 1. Persistent CDP Chrome for the PNG phase — one long-lived headless
    #    Chrome driven over the DevTools Protocol (Page.navigate ->
    #    Page.captureScreenshot) with N parallel tabs, removing the per-slide
    #    fork that caps PNG at ~2x. Falls back to today's parallel
    #    --screenshot path when CDP is unavailable.
    # 2. Streaming ElevenLabs TTS — one WS/stream session per render so paid
    #    narration avoids per-slide HTTP round-trips; the `say` 2x cap only
    #    applies to the free local voice, so this is what lifts paid renders.
    # 3. GPU encode — `-c:v libx264` -> `h264_videotoolbox` (macOS) /
    #    `h264_nvenc` (cloud GPU) to remove CPU saturation in the MP4 phase
    #    (verify hardware output stays concat-safe first).
    # 4. Cloud render queue — horizontal fan-out of per-slide
    #    (PNG -> TTS -> MP4) tasks to many workers over a queue, with concat +
    #    caption burn as the single serial join. Reuses batch.py's cost
    #    estimate + spend ceiling; this is the step that reaches the full 10x.
)

__all__ = [
    "SAY_SPEEDUP_CEILING",
    "DEFAULT_MAX_WORKERS",
    "cpu_cores",
    "workers_default",
    "effective_workers",
    "tts_workers",
    "map_threads",
    "NEXT_STEPS",
]
