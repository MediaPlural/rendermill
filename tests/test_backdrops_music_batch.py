"""§5.3 backdrop + §5.6 music bed + §5.7 batch tests. The ffmpeg tests run
REAL renders on tiny synthetic sources; the batch tests are pure Python."""
from __future__ import annotations

import subprocess

import pytest


def _make_png(path, w=320, h=240, color=(20, 22, 26)):
    from PIL import Image
    Image.new("RGB", (w, h), color).save(path)
    return path


def _make_video(path, duration_s=2.0, w=320, h=240, pattern="testsrc2"):
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"{pattern}=duration={duration_s}:size={w}x{h}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path),
    ], check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return path


def _make_m4a(path, duration_s=1.5):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=660:duration={duration_s}",
        "-c:a", "aac", str(path),
    ], check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return path


def test_backdrop_cmd_shape():
    from rendermill.ffmpeg_video import _backdrop_slide_cmd
    cmd = _backdrop_slide_cmd("s.png", "bd.mp4", "n.m4a", "o.mp4", 1920, 1080, 3.2)
    joined_list = " ".join(cmd)
    assert "-stream_loop" in joined_list and "-1" in cmd
    joined = joined_list
    assert "scale=1920:1080" in joined
    assert "crop=1920:1080" in joined
    assert "overlay=0:0" in joined


def test_backdrop_slide_render_real(tmp_path):
    """§5.3: real ffmpeg — slide PNG over looping b-roll, audio concat."""
    from rendermill.ffmpeg_video import make_slide_video
    from rendermill.cache import Cache
    png = _make_png(tmp_path / "slide.png")
    bd = _make_video(tmp_path / "broll.mp4", duration_s=2.0)
    m4a = _make_m4a(tmp_path / "narr.m4a", duration_s=3.0)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    slide = {"backdrop": str(bd)}
    out = make_slide_video(png, m4a, 3.0, 0, slide=slide, workdir=workdir,
                           cache=Cache(workdir), dimensions=(320, 240))
    assert out.exists()
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True)
    # pad = dur + 0.15
    assert float(res.stdout.strip()) == pytest.approx(3.15, abs=0.3)
    # audio stream present
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True)
    assert "aac" in res.stdout


def test_music_bed_mix_real(tmp_path):
    """§5.6: real ffmpeg amix — narration master + bed track."""
    from rendermill.ffmpeg_video import mix_music_bed
    master = _make_video(tmp_path / "master.mp4", duration_s=2.0)
    bed = _make_m4a(tmp_path / "bed.m4a", duration_s=1.0)  # shorter: loops
    out = tmp_path / "mixed.mp4"
    mix_music_bed(master, bed, out, gain=0.12, ducking=0.5, duration=2.0)
    assert out.exists()
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True)
    assert float(res.stdout.strip()) == pytest.approx(2.0, abs=0.2)


def test_music_bed_static_gain_real(tmp_path):
    from rendermill.ffmpeg_video import mix_music_bed
    master = _make_video(tmp_path / "m.mp4", duration_s=1.5)
    bed = _make_m4a(tmp_path / "b.m4a", duration_s=1.5)
    out = mix_music_bed(master, bed, tmp_path / "o.mp4", gain=0.2, ducking=0)
    assert out.exists()


# ---------------------------------------------------------------------------
# §5.7 batch
# ---------------------------------------------------------------------------

def test_estimate_free_mode_is_zero():
    from rendermill.batch import estimate_project
    rep = estimate_project([{"narration": "x" * 500}], {"mode": "free"})
    assert rep.est_usd == 0.0


def test_estimate_local_is_zero(monkeypatch):
    from rendermill import batch, tts, keys
    monkeypatch.setattr(keys, "get", lambda name: None)
    monkeypatch.setattr(tts, "resolve_provider", lambda cfg: "local")
    rep = batch.estimate_project([{"narration": "x" * 500}], {})
    assert rep.est_usd == 0.0


def test_estimate_elevenlabs_counts_chars(monkeypatch):
    from rendermill import batch, tts
    monkeypatch.setattr(tts, "resolve_provider", lambda cfg: "elevenlabs")
    rep = batch.estimate_project(
        [{"narration": "x" * 1000}], {"tts": {"model": "eleven_v3"}})
    assert rep.est_usd_tts == pytest.approx(0.10, rel=0.01)


def test_ceiling_check_halts():
    from rendermill.batch import check_ceiling, CeilingExceeded, CostReport
    rep = CostReport("proj", "elevenlabs", 1000, 0.10, 0.0, 0.0)
    check_ceiling(1.00, 0.85, rep)  # 0.95 <= 1.00: proceeds
    with pytest.raises(CeilingExceeded, match=r"yellow-gate"):
        check_ceiling(1.00, 0.95, rep)  # 1.05 > 1.00: halt


def test_resolve_ceiling_order(monkeypatch):
    from rendermill.batch import resolve_ceiling
    monkeypatch.delenv("RENDERMILL_SPEND_CEILING", raising=False)
    assert resolve_ceiling(None, None) == 1.00
    assert resolve_ceiling(5.0, None) == 5.0
    assert resolve_ceiling(5.0, 2.0) == 2.0
    monkeypatch.setenv("RENDERMILL_SPEND_CEILING", "3.50")
    assert resolve_ceiling(None, None) == 3.50