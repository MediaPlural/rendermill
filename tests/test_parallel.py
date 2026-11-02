"""Tests for the phase-parallel worker pool (rendermill.parallel) + its wiring
into core (render_pngs / make_audio_batch) and the cache's per-slot lock.

No Chrome / `say` / ffmpeg is run here — the subprocess-bound primitives are
monkeypatched so the tests stay fast and deterministic; the real subprocesses
are exercised by scripts/benchmark_parallel.py and test_say_tts.py.
"""
from __future__ import annotations

import threading
import time

from rendermill import core, parallel, tts
from rendermill.cache import Cache


# ---------------------------------------------------------------------------
# rendermill.parallel — worker-count policy
# ---------------------------------------------------------------------------

def test_import_exposes_public_api():
    assert hasattr(parallel, "map_threads")
    assert hasattr(parallel, "effective_workers")
    assert hasattr(parallel, "tts_workers")
    assert parallel.SAY_SPEEDUP_CEILING == 2.0
    assert parallel.workers_default() >= 1


def test_effective_workers_clamps():
    cores = parallel.cpu_cores()
    assert parallel.effective_workers(None) == parallel.workers_default()
    assert parallel.effective_workers(0) == parallel.workers_default()
    assert parallel.effective_workers(1) == 1
    assert parallel.effective_workers(10 ** 6) == cores


def test_tts_workers_passes_full_count_to_local_say():
    """The `say` lock caps *speedup* (~2x), not workers — capping workers to 2
    leaves throughput on the table (reaches only ~1.8x)."""
    assert parallel.tts_workers("local", 8) == 8
    assert parallel.tts_workers("macos", 16) == 16
    assert parallel.tts_workers("elevenlabs", 4) == 4
    assert parallel.tts_workers("deepgram", 4) == 4


# ---------------------------------------------------------------------------
# rendermill.parallel — map_threads
# ---------------------------------------------------------------------------

def test_map_threads_preserves_order():
    assert parallel.map_threads(lambda i: i * 2, range(8), workers=4) == \
        [0, 2, 4, 6, 8, 10, 12, 14]


def test_map_threads_sequential_inline_matches():
    assert parallel.map_threads(lambda i: i + 1, [3, 1, 2], workers=1) == [4, 2, 3]


def test_map_threads_runs_concurrently():
    """Deterministic concurrency proof: a Barrier(3) only releases when all
    three tasks are in-flight at once — an accidentally-inline worker pool
    would block the first task and raise BrokenBarrierError."""
    barrier = threading.Barrier(3)
    out = parallel.map_threads(
        lambda i: (barrier.wait(timeout=3), i)[1], range(3), workers=3)
    assert out == [0, 1, 2]


# ---------------------------------------------------------------------------
# cache — per-slot lock (parallel TTS must not race identical narrations)
# ---------------------------------------------------------------------------

def test_cache_slot_lock_generates_once_for_identical_key(tmp_path):
    cache = Cache(tmp_path)
    calls = {"n": 0}
    lock = threading.Lock()

    def gen(out_path):
        with lock:
            calls["n"] += 1
        time.sleep(0.02)
        out_path.write_bytes(b"x")

    results = parallel.map_threads(
        lambda _: cache.run("tts", ["same", "text"], "bin", gen),
        range(4), workers=4,
    )
    assert calls["n"] == 1, "identical key must produce the artifact once"
    assert len({str(p) for p in results}) == 1
    assert all(p.exists() for p in results)


# ---------------------------------------------------------------------------
# core wiring — render_pngs / make_audio_batch accept and use `workers`
# ---------------------------------------------------------------------------

def test_render_pngs_parallel_returns_in_order(tmp_path, monkeypatch):
    from rendermill import renderer_html
    slides = []
    for i in range(4):
        h = tmp_path / f"s{i}.html"
        h.write_text("<html></html>")
        slides.append({"type": "html", "html": str(h)})

    def fake_render(html_path, png_path, width, height):
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return png_path

    monkeypatch.setattr(renderer_html, "render_html_to_png", fake_render)
    pngs = core.render_pngs(slides, workdir=tmp_path, project_dir=None,
                             dimensions=(100, 100), workers=4)
    assert [p.name for p in pngs if p is not None] == \
        [f"slide_{i:02d}.png" for i in range(4)]
    assert all(p is not None and p.exists() for p in pngs)


def test_make_audio_batch_parallel_ordered(tmp_path, monkeypatch):
    workdir = tmp_path / "w"
    workdir.mkdir()
    cache = Cache(workdir)
    slides = [{"narration": f"line {i}"} for i in range(4)]
    calls = []

    def fake_make_audio(narration, idx, *, workdir, cache, tts_cfg):
        calls.append(idx)
        return (workdir / f"narr_{idx:02d}.m4a"), float(idx + 1)

    monkeypatch.setattr(core.tts_mod, "make_audio", fake_make_audio)
    results = core.make_audio_batch(
        slides, workdir=workdir, cache=cache,
        tts_cfg={"provider": "elevenlabs", "speaking_rate": 1.0},
        resolved_provider="elevenlabs", rates=[1.0] * 4, workers=4,
    )
    assert [d for _, d in results] == [1.0, 2.0, 3.0, 4.0]
    assert sorted(calls) == [0, 1, 2, 3]


def test_make_audio_batch_rejects_missing_narration(tmp_path):
    workdir = tmp_path / "w"
    workdir.mkdir()
    cache = Cache(workdir)
    slides = [{"narration": "ok"}, {"narration": "   "}]
    try:
        core.make_audio_batch(
            slides, workdir=workdir, cache=cache,
            tts_cfg={"provider": "local"}, resolved_provider="local",
            rates=[1.0, 1.0], workers=2,
        )
    except ValueError as e:
        assert "slide 1" in str(e)
    else:
        raise AssertionError("expected ValueError for blank narration")


def test_make_audio_batch_notes_say_speedup_ceiling(tmp_path, monkeypatch, capsys):
    workdir = tmp_path / "w"
    workdir.mkdir()
    cache = Cache(workdir)
    slides = [{"narration": f"line {i}"} for i in range(3)]

    def fake_make_audio(narration, idx, *, workdir, cache, tts_cfg):
        return (workdir / f"narr_{idx:02d}.m4a"), 1.0

    monkeypatch.setattr(core.tts_mod, "make_audio", fake_make_audio)
    core.make_audio_batch(
        slides, workdir=workdir, cache=cache,
        tts_cfg={"provider": "local"}, resolved_provider="local",
        rates=[1.0] * 3, workers=8,
    )
    out = capsys.readouterr().out
    assert "caps concurrent synthesis" in out
    assert "speedup" in out
