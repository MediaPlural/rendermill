"""§5.4 clips tests: cut-list arithmetic + real ffmpeg segment cuts."""
from __future__ import annotations

import json
import subprocess

import pytest


def _make_test_master(tmp_path, duration_s=6.0):
    """A real 6s test source (testsrc + sine) via ffmpeg."""
    out = tmp_path / "master.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size=320x240:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(out),
    ], check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return out


def test_build_clips_hook_and_slides():
    from rendermill.clips import build_clips
    slides = [
        {"narration": "hook"},
        {"narration": "mid", "role": "question"},
        {"narration": "end", "role": "cta"},
    ]
    durs = [2.0, 3.0, 1.5]
    clips = build_clips(slides, durs)
    roles = [c.role for c in clips]
    assert roles[0] == "hook"
    assert "question" in roles
    assert roles.count("cta") == 1  # declared cta, not duplicated
    # hook spans [0, 2.15)
    assert clips[0].start_s == 0.0
    assert clips[0].end_s == pytest.approx(2.15)
    # slide 1 starts at 2.15
    assert clips[1].start_s == pytest.approx(2.15)


def test_build_clips_synthetic_cta():
    from rendermill.clips import build_clips
    clips = build_clips([{"narration": "a"}, {"narration": "b"}], [1.0, 1.0])
    assert [c.role for c in clips] == ["hook", "slide_01", "cta"]


def test_emit_clips_real_ffmpeg(tmp_path):
    from rendermill.clips import build_clips, emit_clips
    master = _make_test_master(tmp_path)
    slides = [
        {"narration": "hook text", "role": "hook"},
        {"narration": "body text", "role": "beat"},
        {"narration": "do the thing", "role": "cta"},
    ]
    # durations relative to the 6s master: 1.0 + 2.0 + 3.0 - 3*0.15 pad
    clips = build_clips(slides, [0.85, 1.85, 2.85])
    clips_dir = tmp_path / "clips"
    manifest, files = emit_clips(master, clips, clips_dir, stem="seg")
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert len(data["segments"]) == len(files) == 3
    for f in files:
        assert f.exists() and f.stat().st_size > 0
        # timestamp baked into the filename
        assert "_" in f.name
    # hook cut duration sanity via ffprobe
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(files[0])],
        capture_output=True, text=True)
    dur = float(res.stdout.strip())
    assert 0.5 <= dur <= 1.5


def test_clips_cfg_resolution():
    from rendermill.clips import clips_cfg
    assert clips_cfg({}) is None
    assert clips_cfg({"clips": False}) is None
    assert clips_cfg({"clips": True}) == {"emit": True}
    assert clips_cfg({"clips": {"emit": True, "dir": "cuts"}})["dir"] == "cuts"
    assert clips_cfg({"clips": {"emit": False}}) is None