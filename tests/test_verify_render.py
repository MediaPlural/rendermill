"""The verifier is the half the §5.2 gates don't cover: a render can pass
every preflight gate and still ship a file whose captions never burned.

These tests pin the new teeth the QA audit demanded: per-chunk sampling
across all caption midpoints, with a threshold that scales so 1-word widow
chunks still pass when present and the WHOLE 75%-missing poisoned master
fails. The previous version of `check_pixels` sampled exactly ONE
timestamp (verify_render.py:173-181 — now obsoleted) and read the burn
status straight from the report it was supposed to be verifying.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/ is not on sys.path by default; add the project root so the
# `from scripts import verify_render as vr` form resolves consistently
# whether pytest is run from the project root or from inside tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from scripts import verify_render as vr  # noqa: E402


def _chunk(start, end, text):
    return (start, end, text)


def test_parse_srt_round_trip(tmp_path):
    srt = tmp_path / "captions.srt"
    srt.write_text(
        "1\n"
        "00:00:00,500 --> 00:00:02,000\n"
        "Hello world.\n"
        "\n"
        "2\n"
        "00:00:02,500 --> 00:00:04,000\n"
        "Second caption, two lines.\n"
        "Continued.\n"
        "\n"
    )
    chunks = vr.parse_srt(srt)
    assert len(chunks) == 2
    assert chunks[0] == (0.5, 2.0, "Hello world.")
    assert chunks[1] == (2.5, 4.0, "Second caption, two lines. Continued.")


def test_parse_srt_ignores_malformed_blocks(tmp_path):
    srt = tmp_path / "captions.srt"
    srt.write_text(
        "1\n"
        "not-a-timestamp\n"
        "ignored\n"
        "\n"
        "2\n"
        "00:00:02,000 --> 00:00:04,000\n"
        "kept\n"
    )
    chunks = vr.parse_srt(srt)
    assert chunks == [(2.0, 4.0, "kept")]


def test_chunk_diff_threshold_passes_single_word_widow():
    """The shipped demo's 'all.' caption measured ~0.42% band diff at
    t=40.05s (audit F1 §'bonus fact'); the threshold for 4 chars must sit
    BELOW that so a present widow passes.
    """
    threshold = vr.chunk_diff_threshold("all.")
    assert threshold < 0.0042, (
        f"threshold {threshold * 100:.2f}% would false-FAIL the shipped "
        f"demo's widow chunk (measured 0.42%)"
    )
    assert threshold >= 0.0035, (
        f"threshold {threshold * 100:.2f}% has no gap over 0.00% — missing "
        f"chunks need a clear failure band"
    )


def test_chunk_diff_threshold_blocks_missing_chunks():
    """A 0.00% band diff (a chunk whose burn silently failed) must fail the
    threshold for ANY caption length."""
    for text in ("all.", "ok.", "yes.",
                 "But encoders round to frame boundaries.",
                 "Word count,"):
        threshold = vr.chunk_diff_threshold(text)
        assert 0.0 < threshold, f"threshold should require POSITIVE diff for {text!r}"


def test_chunk_diff_threshold_scales_with_chars():
    """Longer captions draw more glyph pixels and tolerate a higher
    threshold; the slope must be positive so the per-chunk check doesn't
    over-tighten as captions get bigger."""
    short = vr.chunk_diff_threshold("ok")
    long = vr.chunk_diff_threshold("the real file drifted by up to twenty six ms")
    assert long > short, (
        f"threshold must increase with text size: short={short}, long={long}"
    )


def test_resolve_captions_srt_prefers_project_dir(monkeypatch, tmp_path):
    """The verifier must find the SRT sidecar regardless of whether the
    MP4 was passed via `--mp4` or discovered via the report's out_path."""
    proj = tmp_path / "demo"
    proj_video = proj / "video_build"
    proj_video.mkdir(parents=True)
    srt = proj_video / "captions.srt"
    srt.write_text("# dummy\n")

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    mp4 = other_dir / "weird-location.mp4"

    found = vr._resolve_captions_srt(mp4, project_dir=proj)
    assert found == srt


def test_resolve_captions_srt_falls_back_to_mp4_paths(tmp_path):
    """When no project_dir is supplied, the resolver still finds the SRT
    alongside the MP4 or in a sibling layout."""
    proj = tmp_path / "demo"
    (proj / "out").mkdir(parents=True)
    (proj / "video_build").mkdir(parents=True)
    srt = proj / "video_build" / "captions.srt"
    srt.write_text("# dummy\n")
    mp4 = proj / "out" / "demo.mp4"
    found = vr._resolve_captions_srt(mp4)
    assert found == srt


def test_resolve_intermediate_video_prefers_project_dir(monkeypatch, tmp_path):
    proj = tmp_path / "demo"
    (proj / "video_build").mkdir(parents=True)
    raw = proj / "video_build" / "concat_raw.mp4"
    raw.write_bytes(b"x")
    other = tmp_path / "elsewhere"
    other.mkdir()
    found = vr._resolve_intermediate_video(other / "x.mp4", project_dir=proj)
    assert found == raw


# ---------- check_pixels: per-chunk iteration (F1 mutation-killer) ----------

def _stub_project(tmp_path):
    """Stand up a project with a 4-chunk SRT, a raw MP4, and an output MP4.

    Used by the check_pixels tests that monkeypatch _frame_rgb to avoid
    actually decoding video.
    """
    proj = tmp_path / "demo"
    (proj / "video_build").mkdir(parents=True)
    (proj / "out").mkdir(parents=True)
    (proj / "video_build" / "captions.srt").write_text(
        "1\n00:00:00,500 --> 00:00:02,000\nFirst chunk burned.\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nSecond chunk poison.\n\n"
        "3\n00:00:04,500 --> 00:00:06,000\nThird chunk poison.\n\n"
        "4\n00:00:06,500 --> 00:00:08,000\nFourth chunk poison.\n\n"
    )
    (proj / "video_build" / "concat_raw.mp4").write_bytes(b"x")
    mp4 = proj / "out" / "demo.mp4"
    mp4.write_bytes(b"x")
    report = {
        "timeline": {
            "spans": [{"start_s": 0.0, "end_s": 8.0, "audio_s": 8.0}],
        },
        "theme": "amber",
    }
    return proj, mp4, report


def _pixels(byte=0x10):
    """A 240x135 RGB frame of uniform bytes — diff against itself is 0%."""
    return bytes([byte]) * (240 * 135 * 3)


def test_check_pixels_iterates_every_chunk(monkeypatch, tmp_path):
    """The poisoned-master scenario from QA audit F1 only fails the render
    verifier if `check_pixels` samples the SRT for every chunk, not just
    the first. This test pins the iteration: a stub that simulates every
    band being IDENTICAL to raw must fail with mentions of EVERY chunk,
    proving the loop touched each one."""
    proj, mp4, report = _stub_project(tmp_path)
    report["theme"] = None  # skip the bonus bg-match check

    sampled_at = []

    def fake_frame_rgb(path, at_s, width=240, height=135, text=True):
        sampled_at.append((path, at_s))
        return (width, height, _pixels(0x10))

    monkeypatch.setattr(vr, "_frame_rgb", fake_frame_rgb)
    # Avoid the theme probe side-effect — we want a tight loop test.
    monkeypatch.setattr(vr, "_resolve_intermediate_video",
                        lambda m, p: tmp_path / "ignored")
    # Force the diff path so we exercise the iteration cheaply without
    # needing distinct raw / mp4 frames.

    with pytest.raises(vr.CheckFailed, match="captions did not burn"):
        vr.check_pixels(mp4, report, project_dir=proj)

    # Midpoints of the four captions: (1.25, 3.25, 5.25, 7.25) — clipped
    # inside [0.05, total-0.05]=[0.05, 7.95]. The loop must hit every one.
    seen = {round(t, 2) for _, t in sampled_at}
    for midpoint in (1.25, 3.25, 5.25, 7.25):
        assert midpoint in seen, (
            f"check_pixels did not sample the chunk at {midpoint}s; "
            f"saw {sorted(seen)!r}. A revert to single-sample would pass "
            f"this proof but fail the poisoned-master integration test."
        )


def test_check_pixels_does_not_fail_when_all_chunks_burn(monkeypatch, tmp_path):
    """When every chunk draws ink (i.e., its band pixels differ from raw
    by more than the threshold), check_pixels returns a success message
    listing how many captions were sampled. Regression-pins that we
    haven't over-tightened the threshold."""
    proj, mp4, report = _stub_project(tmp_path)
    report["theme"] = None  # skip the bonus bg-match check

    # Two distinct "frames": burned (different bytes from raw) vs raw.
    raw_bytes = _pixels(0x10)
    burn_bytes = _pixels(0xC0)

    def fake_frame_rgb(path, at_s, width=240, height=135, text=True):
        # The raw intermediate always returns dark pixels; the rendered
        # MP4 returns bright pixels in the band. The diff approximation
        # here stands in for the real libass band; check_pixels only
        # operates on band pixels, and 60% of band pixels differ.
        path_str = str(path)
        if "concat_raw" in path_str:
            return (width, height, raw_bytes)
        return (width, height, burn_bytes)

    monkeypatch.setattr(vr, "_frame_rgb", fake_frame_rgb)
    # Make sure resolution finds our raw stub.
    monkeypatch.setattr(vr, "_resolve_intermediate_video",
                        lambda m, p: tmp_path / "ignored")

    msg = vr.check_pixels(mp4, report, project_dir=proj)
    assert "per-chunk ink confirmed" in msg
    assert "4 captions" in msg
